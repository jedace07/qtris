"""The window a web game runs in.

The web runner starts one process of this package per game, so each game has its own
browser engine as well as its own profile, and Qtris tracks and stops it like any other
game. It uses QWebEngineView from Qt Widgets rather than Qt Quick's WebEngineView, which
refuses pointer lock, and mouse-look games need that.
"""
