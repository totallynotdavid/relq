"""Failures a caller can fix by changing their schema or their type mapping."""


class CodegenError(Exception):
    """The schema or the type mapping cannot produce a safe module.

    The message is written for the person running ``relq-codegen``, so the CLI
    prints it as one line. Programming errors in relq-codegen itself stay
    ``ValueError`` and keep their traceback.
    """
