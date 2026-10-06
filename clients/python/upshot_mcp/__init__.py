"""upshot-mcp: Upshot's transcription, as MCP tools over stdio (D86).

The standard library only, on purpose: the `mcp` package imports its HTTP transports,
cryptography and the Windows event-log and WMI modules whatever part of it is used, which
made the frozen bridge 40 MB, 76 MB a process, and read like host reconnaissance. The
stdio subset a tool server needs is small, and the official client tests it.
"""

__version__ = "0.1.0"
