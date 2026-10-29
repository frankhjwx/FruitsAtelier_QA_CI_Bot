# Third-party dependencies

The runtime installs the versions pinned in `requirements.txt` from PyPI.
Dependency distributions retain their bundled license texts. They are installed
separately and are not vendored into this repository.

- [discord.py 2.7.1](https://pypi.org/project/discord.py/2.7.1/): MIT,
  copyright Rapptz and contributors.
- [aiohttp 3.14.3](https://pypi.org/project/aiohttp/3.14.3/): Apache-2.0 AND MIT.

Transitive dependencies are also version-pinned in `requirements.txt`; their
installed distribution metadata and license files are authoritative. The Bot
uses the editor's existing build and regression entry points on
GitHub-hosted runners. Editor packages retain the editor's third-party notices
and corresponding-source attachments.
