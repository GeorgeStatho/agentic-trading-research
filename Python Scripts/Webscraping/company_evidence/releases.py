"""Conservative release extraction. Ambiguous prose remains an explicit gap."""
from datetime import date
from html.parser import HTMLParser
import json
import re

from research_foundation_contracts import timestamp


PERIOD = r'(?:Q[1-4]\s+(?:FY\s*|fiscal\s+)?20\d{2}|(?:FY\s*|fiscal\s+)20\d{2})'
NUMBER = r'-?\d+(?:,\d{3})*(?:\.\d+)?'
METRICS = {'revenue': 'revenue', 'diluted eps': 'eps_diluted', 'eps': 'eps_diluted',
           'gross margin': 'gross_margin', 'operating margin': 'operating_margin'}
SCALES = {'billion': 1e9, 'million': 1e6, 'thousand': 1e3, '': 1}


class ReleaseHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text, self.events = [], []
        self.hidden = None
        self.event_script = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        # ix:header is inline XBRL's hidden block of tagged values (megabytes in a large filer's exhibit).
        if tag in ('script', 'style', 'ix:header') and self.hidden is None:
            self.hidden = tag
            self.event_script = tag == 'script' and dict(attrs).get('type') == 'application/ld+json'

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden, self.event_script = None, False

    def handle_data(self, data):
        if self.event_script:
            self.events.append(data)
        elif self.hidden is None:
            self.text.append(data)


def release_text(html):
    return re.sub(r'\s+', ' ', ' '.join(ReleaseHTML(html).text)).strip()


def guidance_facts(text):
    facts = []
    # Delimit on sentences, but do not split decimal numbers.
    for sentence in re.finditer(r'.+?(?:[.!?](?=\s+[A-Z]|$)|$)', text):
        value = sentence.group()
        periods = re.findall(PERIOD, value, re.I)
        if len(periods) != 1 or not re.search(r'guidance|expects?|outlook|forecast', value, re.I):
            continue
        for label, metric in METRICS.items():
            if label == 'eps' and re.search('diluted eps', value, re.I):
                continue
            match = re.search(r'\b' + label + r'\b[^.;]{0,65}?\$?(' + NUMBER + r')\s*'
                              r'(billion|million|thousand)?\s*(?:%\s*)?'
                              r'(?:to|[-–])\s*\$?(' + NUMBER + r')\s*(billion|million|thousand|%)?', value, re.I)
            if not match:
                continue
            basis = ('adjusted' if re.search(r'adjusted|non[ -]GAAP', value, re.I)
                     else 'US-GAAP' if re.search(r'\bGAAP\b', value, re.I) else 'unspecified')
            # EPS/margins without an explicit basis must never acquire a made-up one.
            if basis == 'unspecified' and metric != 'revenue':
                continue
            is_margin = metric.endswith('margin')
            low_scale, high_scale = (match[2] or '').lower(), (match[4] or '').lower()
            if is_margin and '%' not in match[0]:
                continue
            if not is_margin and ('$' not in match[0] or not re.search(r'\bUSD\b|U\.S\. dollars|US\$', text, re.I)):
                continue
            scale = SCALES.get(high_scale if high_scale != '%' else '', 1)
            if low_scale and high_scale and low_scale != high_scale:
                continue
            scale = SCALES.get(low_scale, scale) if low_scale else scale
            year = re.search(r'20\d{2}', periods[0])[0]
            quarter = re.search(r'Q[1-4]', periods[0], re.I)
            period = f'FY{year}-{quarter[0].upper()}' if quarter else f'FY{year}'
            prior = bool(re.search(r'previous|prior', value, re.I))
            facts.append({'metric': metric, 'kind': 'prior_guidance' if prior else 'guidance',
                          'value': float(match[1].replace(',', '')), 'range_high': float(match[3].replace(',', '')),
                          'unit': 'percent' if is_margin else 'USD/shares' if metric == 'eps_diluted' else 'USD',
                          'currency': None if is_margin else 'USD', 'scale': scale,
                          'fiscal_period': period, 'period_type': 'quarter' if quarter else 'annual',
                          'accounting_basis': basis, 'source_start': sentence.start(), 'source_end': sentence.end(),
                          'extraction_version': 'explicit-guidance-range-v1'})
    return tuple(facts)


def scheduled_events(html):
    """Only publisher-supplied Event markup; date-only values remain date-only."""
    events = []
    for script in ReleaseHTML(html).events:
        try:
            data = json.loads(script)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, (list, dict)):
            continue
        items = data if isinstance(data, list) else data.get('@graph', [data])
        for item in items:
            if not isinstance(item, dict) or item.get('@type') != 'Event':
                continue
            if not re.search(r'earnings|financial results', item.get('name', ''), re.I):
                continue
            start = item.get('startDate', '')
            try:
                event_time = timestamp(start) if 'T' in start else date.fromisoformat(start).isoformat()
            except (ValueError, TypeError):
                continue
            cancelled = str(item.get('eventStatus', '')).endswith(('EventCancelled', 'EventPostponed'))
            events.append({'name': item['name'], 'scheduled_at': event_time,
                           'precision': 'timestamp' if 'T' in start else 'date',
                           'confirmed': not cancelled, 'status': item.get('eventStatus', 'publisher_announced'),
                           'uncertainty': 'time_not_supplied' if 'T' not in start else 'publisher_schedule_may_change'})
    return tuple(events)
