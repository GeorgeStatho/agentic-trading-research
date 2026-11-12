"""The sign of a call or put trade, shared by the shadow gates, the pick score and the follower stop."""

TRADE_SIDES = {'call': 1, 'put': -1}


def trade_side(decision):
    """+1 for a call, -1 for a put, None for any other decision; multiply a move by it to sign it by the trade."""
    return TRADE_SIDES.get(str(decision or '').strip().lower())
