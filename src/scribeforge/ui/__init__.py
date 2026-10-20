"""Desktop shell for ScribeForge.

`viewmodel` holds the framework-independent presentation logic (unit tested);
`tk_window` is the thin Tkinter layer, imported lazily so the CLI keeps working
on Python builds without Tkinter and CI never needs a display.
"""
