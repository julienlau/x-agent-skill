---
short: {{short}}
handle: {{handle}}
voice: {{voice}}
timezone: {{timezone}}
poster: {{poster}}
---

# x-{{name}}

Every skill in the pack reads the block above through `account.py`.

- `short`: the short name you switch with ("post this as {{short}}",
  `account.py use {{short}}`). It must be unique across accounts.
- `handle`: the @handle. Before publishing, `/x-post` checks it against
  `twitter whoami` and stops if they differ.
- `voice`: the default voice, a file in `<home>/voices/`. A voice named in
  the request overrides it.
- `timezone`: the zone for posting times in `plan.md`.
- `poster`: how `/x-post` publishes after your "yes". `twitter-cli` posts
  with twitter-cli, `browser` posts through the agent's browser tool with
  the cookie header, `none` stops at copy-ready text. `auto` picks
  twitter-cli if it is installed and `twitter.env` exists, else browser if
  `twitter.env` has a cookie, else none.

`twitter.env` (optional, `chmod 600`, never commit it, never read by the
agent directly, loaded by `account.py`). One of:

    TWITTER_COOKIE="auth_token=...; ct0=...; twid=..."

the raw `Cookie` header of a logged-in x.com request (browser devtools,
Network tab). It serves both twitter-cli and browser publishing. Or:

    TWITTER_AUTH_TOKEN=...
    TWITTER_CT0=...

or, twitter-cli only, to read cookies from a browser profile:

    TWITTER_BROWSER=chrome
    TWITTER_CHROME_PROFILE=Profile 2

## Niche and audience

## Notes
