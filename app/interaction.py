"""Cancellation shared by console prompts and action reporting."""
class OperationCancelled(Exception):
    pass


def ask(prompt):
    value = input(prompt)
    if value.strip().lower() in {"/back", "назад", "отмена", "/cancel"}:
        raise OperationCancelled()
    return value
