import requests


Websites={
    "https://www.economist.com/",
    "https://www.bloomberg.com/",
    "https://www.reuters.com/markets/",
    "https://www.marketwatch.com/",
    "https://finance.yahoo.com/",
    "https://www.wsj.com/news/business",
    "https://www.ft.com/markets",
    "https://www.cnbc.com/finance/",
    "https://www.investing.com/",
    "https://www.fool.com/",
    "https://www.barrons.com/",
    "https://www.morningstar.com/",
    "https://www.thestreet.com/",
    "https://www.zacks.com/",
    "https://www.businessinsider.com/markets"
}


ECONOMIST_CATEGPORIES=[
    "Weeklyedition",
    "the-world-in-brief",
    "topics"
]

ECONOMIST_TOPICS=[
    "united-states",
    "china",
    "Business",
    "finance-and-economics",
    "europe",
    "asia",
    "middle-east",
    "the-americas",
    "artificial-intelligence",
    "culture"
]



BLOOMBERG_CATEGORIES=[
    "Markets",
    "Economics",
    "Industries",
    "Tech",
    "Politics",
    "Businessweek",
    "Opinion",
    "Video",
    "More"
]

industries = {
    "Agriculture": ["Cal-Maine Foods", "Alico", "Limoneira", "Vital Farms"],
    "Oil and Gas": ["Baker Hughes", "Diamondback Energy", "APA", "Epsilon Energy"],
    "Renewable Energy": ["First Solar", "Enphase Energy", "Sunrun", "Array Technologies", "Canadian Solar"],
    "Chemicals": ["Balchem", "Hawkins", "Codexis", "Aspen Aerogels", "Danimer Scientific"],
    "Construction": ["Sterling Infrastructure", "Willdan Group", "Limbach Holdings", "Concrete Pumping Holdings", "Great Lakes Dredge & Dock"],
    "Manufacturing": ["Honeywell", "Woodward", "Proto Labs", "Fabrinet", "Advanced Energy Industries"],
    "Automotive": ["Tesla", "PACCAR", "Rivian", "Lucid Group", "Autoliv"],
    "Aerospace and Defense": ["Axon Enterprise", "Rocket Lab", "Kratos Defense", "Astronics", "Mercury Systems"],
    "Transportation and Logistics": ["C.H. Robinson", "Old Dominion Freight Line", "Saia", "Ryanair", "Heartland Express"],
    "Retail": ["Costco", "Ross Stores", "Ulta Beauty", "O'Reilly Automotive", "Walgreens Boots Alliance"],
    "E-commerce": ["Amazon", "eBay", "Booking Holdings", "JD.com", "PDD Holdings"],
    "Consumer Goods": ["PepsiCo", "Mondelez", "Keurig Dr Pepper", "Kraft Heinz", "Celsius Holdings"],
    "Hospitality and Travel": ["Marriott International", "Airbnb", "Trip.com", "Booking Holdings", "Expedia Group"],
    "Real Estate": ["CoStar Group", "Zillow", "Equinix", "SBA Communications", "Lamar Advertising"],
    "Banking": ["Bank OZK", "East West Bancorp", "First Citizens BancShares", "BancFirst", "Atlantic Union Bankshares"],
    "Insurance": ["Erie Indemnity", "Selective Insurance Group", "Palomar Holdings", "Root", "Goosehead Insurance"],
    "Asset Management": ["T. Rowe Price", "SEI Investments", "Victory Capital", "Virtus Investment Partners", "Artisan Partners"],
    "Financial Services": ["PayPal", "Coinbase", "Robinhood", "Interactive Brokers", "Nasdaq"],
    "Fintech": ["PayPal", "Coinbase", "Robinhood", "SoFi Technologies", "Marqeta"],
    "Technology": ["Apple", "Microsoft", "Alphabet", "Amazon", "Meta Platforms"],
    "Semiconductors": ["NVIDIA", "Broadcom", "AMD", "Intel", "Qualcomm"],
    "Software": ["Microsoft", "Adobe", "Intuit", "Autodesk", "Palo Alto Networks"],
    "Artificial Intelligence": ["NVIDIA", "Microsoft", "Alphabet", "Palantir", "SoundHound AI"],
    "Telecommunications": ["T-Mobile US", "Comcast", "Charter Communications", "Liberty Broadband", "Iridium Communications"],
    "Media": ["Netflix", "Comcast", "Fox", "Sirius XM", "Warner Music Group"],
    "Entertainment": ["Netflix", "Electronic Arts", "Take-Two Interactive", "Warner Music Group", "DraftKings"],
    "Healthcare": ["Amgen", "Gilead Sciences", "Vertex Pharmaceuticals", "Regeneron", "Illumina"],
    "Pharmaceuticals": ["Amgen", "Gilead Sciences", "Vertex Pharmaceuticals", "Jazz Pharmaceuticals", "Moderna"],
    "Biotechnology": ["Biogen", "Moderna", "Regeneron", "Alnylam Pharmaceuticals", "Exelixis"],
    "Medical Devices": ["Intuitive Surgical", "DexCom", "Align Technology", "Insulet", "IDEXX Laboratories"],
    "Education": ["Duolingo", "Udemy", "Strategic Education", "Lincoln Educational Services", "Laureate Education"],
    "Professional Services": ["Verisk Analytics", "Exponent", "CRA International", "Forrester Research", "Huron Consulting Group"],
    "Industrial Equipment": ["PACCAR", "Nordson", "Lincoln Electric", "Astec Industries", "Tennant Company"],
    "Consumer Electronics": ["Apple", "Garmin", "GoPro", "Sonos", "Logitech"],
    "Apparel and Luxury Goods": ["Lululemon", "Crocs", "Steven Madden", "G-III Apparel Group", "Fossil Group"],
}

