""" Selection payload limits and the shared truncation helper. """

SELECTION_PAYLOAD_NAMES = 200
SELECTION_PAYLOAD_OBJECTS = 50
SELECTION_COLLECTION_ROWS = 50

SUB_SELECTION_CAP = 2000


def cap_selection(values, cap=SUB_SELECTION_CAP):
    values = list(values)
    return values[:cap], len(values) > cap
