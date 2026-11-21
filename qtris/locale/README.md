# Updating translations

Run these commands from the repository root with GNU gettext installed:

```sh
sh utils/update-translation-template.sh
msgmerge --update qtris/locale/fr.po qtris/locale/qtris.pot
msgfmt --check qtris/locale/fr.po -o /dev/null
LANGUAGE=fr uv run qtris
```

Replace `fr` with your language. To create a new catalog, use `msginit` instead
of `msgmerge`, then add the language code to `qtris/locale/LINGUAS`, keeping
the list sorted. Commit only the catalogs you edited and any change to `LINGUAS`.

Qtris compiles edited catalogs on startup and writes them to the user's cache.
The Flatpak bundles precompiled catalogs and does not require `msgfmt` at runtime.
