"""Display precision shared by printable exports; never round stored values."""

DECIMAL_PLACES = 4
NUMBER_FORMAT = '0.0000'
PERCENT_FORMAT = '0.0000%'


def format_number(value):
    return '' if value is None or value == '' else f'{float(value):.{DECIMAL_PLACES}f}'
