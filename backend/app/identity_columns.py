"""Email identity and historical phone index expressions.

phone_identity is retained only for historical migration/index compatibility.
Runtime authentication uses users.phone_e164, never these regional guesses.
"""

from sqlalchemy import case, func
from sqlalchemy.sql.expression import Grouping


def email_identity(column):
    return func.lower(func.trim(column))


def phone_identity(column):
    value = func.trim(column)
    for separator in (" ", "-", "(", ")", ".", "\t", "\r", "\n"):
        value = func.replace(value, separator, "")
    # PostgreSQL requires parentheses around a CASE used as an index expression.
    return Grouping(case(
        (func.substr(value, 1, 2) == "00", "+" + func.substr(value, 3)),
        (
            (func.length(value) == 10) & (func.substr(value, 1, 2).in_(["05", "01"])),
            "+966" + func.substr(value, 2),
        ),
        ((func.length(value) == 9) & (func.substr(value, 1, 1) == "5"), "+966" + value),
        ((func.length(value) == 11) & (func.substr(value, 1, 2) == "03"), "+92" + func.substr(value, 2)),
        ((func.length(value) == 12) & (func.substr(value, 1, 3) == "966"), "+" + value),
        ((func.length(value) == 12) & (func.substr(value, 1, 2) == "92"), "+" + value),
        else_=value,
    ))
