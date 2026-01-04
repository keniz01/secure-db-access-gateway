import os

class ForbiddenSqlStatementException(Exception):
    def __init__(self, message: str):
        super().__init__(message)

    def __str__(self):
        return f"Forbidden SQL statement: {self.args[0]}"

    def __repr__(self):
        return f"{self.__class__.__name__}(message={self.args[0]!r})"
