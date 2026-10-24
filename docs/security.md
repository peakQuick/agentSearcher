[← Back to README](../README.md)

# Security

## SSRF

The server downloads whatever URLs it is sent, so by default it doesn't reach into the internal network. Blocked:
- `localhost` and `127.0.0.0/8`, `::1`;
- private networks `10/8`, `172.16/12`, `192.168/16`, IPv6 ULA `fc00::/7`;
- link-local `169.254/16`, including the cloud metadata address `169.254.169.254`, and `fe80::/10`;
- CGNAT `100.64/10`, `0.0.0.0`, reserved and multicast addresses;
- IPv4 addresses written as IPv6 (`::ffff:127.0.0.1`);
- any scheme other than `http` and `https`.

A host with several DNS addresses is blocked if any one of them is such an address.

Where it is checked:
- **before every curl_cffi request**, including every redirect step: redirects are followed manually, up to 10 steps;
- **after connecting**: the IP that curl actually connected to. This protects against DNS rebinding between the check and the connection. The response body from such an address is not read. If `PROXY_URL` is set, this check is skipped, because the connection goes to the proxy;
- **in the browser**: before opening the page and for every request the page makes (navigations, JS and meta redirects, `fetch`/XHR, subresources). A request to a blocked address is aborted, and the log shows `blocked browser request to ...`.

robots.txt, sitemap, llms.txt and `.md` versions go through the same fetching, so they are protected the same way.

A blocked URL goes to `failed_results` in extract with the error `blocked: private or local address`, is replaced by the snippet in search, and is simply skipped in crawl and map.

`AGENTSEARCHER_ALLOW_PRIVATE_URLS=1` turns the check off entirely, for example for local documentation or an intranet.

Addresses set by the operator are not checked: `LLM_BASE_URL`, `SEARXNG_URL`, `PROXY_URL`. They may point at localhost on purpose.

### Known gaps

- Chromium resolves DNS itself, so rebinding inside the browser (our check saw a public address, but the browser connected to a private one) is not caught.
- A page's WebSocket connections don't go through request interception and are not checked.

## Exposing the server

By default the server listens only on `127.0.0.1`. If you run it with `HOST=0.0.0.0`:
- set `API_KEYS`, otherwise anyone can use the server;
- block outbound traffic to internal networks at the network level (docker network, firewall): this also closes the remaining gaps above;
- keep in mind that every request can start the browser and take ~1 GB of memory. There is no per-client request limit.
