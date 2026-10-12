"""One generation request per process. No provider retries, caching requests or tools."""
import json
import re
import sys
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 128 * 1024
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
USAGE_FIELDS = ('prompt_token_count', 'candidates_token_count', 'thoughts_token_count',
                'cached_content_token_count', 'total_token_count')


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ollama_response(payload):
    request = Request(payload['host'].rstrip('/') + '/api/chat',
                      data=json.dumps({'model': payload['model'], 'messages': payload['messages'],
                                       'format': payload['format'], 'stream': False,
                                       'options': {'temperature': 0, 'num_predict': payload['max_output_tokens']}}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    with build_opener(NoRedirects(), ProxyHandler({})).open(request, timeout=payload['timeout']) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError('Research planner response too large')
    decoded = json.loads(data)
    return {'text': decoded.get('message', {}).get('content', ''),
            'usage': {'prompt_token_count': decoded.get('prompt_eval_count'),
                      'candidates_token_count': decoded.get('eval_count'), 'thoughts_token_count': 0},
            'diagnostics': {'provider': 'ollama', 'finish_reason': decoded.get('done_reason')},
            'error': decoded.get('done_reason') == 'length'}


def thinking_config(payload):
    version = re.match(r'gemini-(\d+)', payload['model'])
    if payload.get('thinking_level') or (version and int(version[1]) >= 3):
        return {'thinking_level': (payload.get('thinking_level') or 'low').upper(), 'include_thoughts': False}
    return {'thinking_budget': payload['thinking_budget'], 'include_thoughts': False}


def vertex_response(payload, *, sdk=None, auth_default=None):
    if sdk is None:
        from google import genai as sdk
    if auth_default is None:
        from google.auth import default as auth_default
    credentials, detected_project = auth_default(scopes=('https://www.googleapis.com/auth/cloud-platform',))
    project = payload.get('project') or detected_project
    if not project:
        raise ValueError('Vertex project is required')
    # Disable SDK retries explicitly. Authentication/setup are included in the parent wall timeout.
    client = sdk.Client(vertexai=True, project=project, location=payload['location'], credentials=credentials,
                        http_options={'api_version': 'v1', 'timeout': max(1, int(payload['timeout'] * 1000)),
                                      'retry_options': {'attempts': 1}, 'client_args': {'follow_redirects': False}})
    try:
        response = client.models.generate_content(model=payload['model'],
            contents=payload['messages'][1]['content'], config={
                'system_instruction': payload['messages'][0]['content'], 'temperature': payload.get('temperature', 0),
                'response_mime_type': 'application/json', 'response_json_schema': payload['format'],
                'candidate_count': 1, 'max_output_tokens': payload['max_output_tokens'],
                'thinking_config': thinking_config(payload),
                'automatic_function_calling': {'disable': True}})
        usage = {field: getattr(response.usage_metadata, field, None) for field in USAGE_FIELDS}
        candidates = response.candidates or []
        finish = getattr(candidates[0], 'finish_reason', None) if len(candidates) == 1 else None
        finish = getattr(finish, 'value', finish)
        text = response.text or ''
        # Only numeric metadata and a bounded text response cross the process boundary.
        return {'text': text if len(text.encode()) <= MAX_RESPONSE_BYTES else '', 'usage': usage,
                'diagnostics': {'provider': 'vertex', 'model_version': getattr(response, 'model_version', None),
                                'project': project, 'location': payload['location'], 'finish_reason': finish},
                'error': finish != 'STOP' or not text or len(text.encode()) > MAX_RESPONSE_BYTES}
    finally:
        client.close()


def dispatch(payload):
    try:
        if payload['provider'] == 'vertex':
            return vertex_response(payload)
        if payload['provider'] == 'ollama':
            return ollama_response(payload)
        raise ValueError('Unsupported research provider')
    except Exception as exc:
        code = getattr(exc, 'code', None)
        code = code if type(code) is int else None
        # Provider exception messages can contain request URLs/credentials; never persist them.
        return {'text': '', 'usage': {}, 'error': True, 'diagnostics': {
            'provider': payload.get('provider'), 'error_type': type(exc).__name__,
            'status_code': code, 'retryable': code in RETRYABLE_STATUS}}


def main():
    payload = json.load(sys.stdin)
    print(json.dumps(dispatch(payload), allow_nan=False))


if __name__ == '__main__':
    main()
