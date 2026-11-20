# i18n Contributor's Guide

**To add or fix a translation, edit [internetarchive/openlibrary-i18n](https://github.com/internetarchive/openlibrary-i18n), not this directory.**

That repository holds the translations for Open Library, one `locale/<lang>/messages.po` per language. They are not committed here: the production image pulls them into `<lang>/messages.po` at build time, and a language whose file fails the build's safety check is left out (it renders in English). To see translations in a local dev environment, run `docker compose run --rm home make i18n-pull`.

A new language needs changes in both repositories, so start by opening an issue on [openlibrary-i18n](https://github.com/internetarchive/openlibrary-i18n/issues).

`messages.pot` in this directory is still the source template. It is regenerated here by the `generate-pot` pre-commit hook, and openlibrary-i18n picks up each change automatically.

For how to write translatable strings in templates and code, see https://docs.openlibrary.org/everyone/internationalization.html
