# AlgoDial Angular frontend

Independent Angular application in this repository, using the SCC frontend's feature-module structure, semantic theme tokens, Material theme bridge and selected shared UI primitives. It contains AlgoDial's existing features only. See [the architecture and feature map](../docs/ANGULAR_FRONTEND.md).

The visual presentation belongs to AlgoDial: its logo, accessible font, teal palette, light/dark surfaces and call-state colours. SCC supplies design references rather than a copied interface. The workspace groups existing tools by task, with searchable navigation, a responsive mobile menu and record editors that keep save controls visible while fields scroll.

Use Node.js 22.23+ and npm. With Django and the normal AlgoDial services running:

```bash
make frontend-build
# Open http://127.0.0.1:8000/app/
```

For Angular development with hot reload, run `make frontend` and open `http://127.0.0.1:4240`. Its proxy forwards API, WebSocket, phone-library and media requests to Django on 8000. Production builds use `/app/` as their base path and the existing origin; no extra production server is required.

```bash
npm run typecheck --prefix frontend
npm test --prefix frontend
npm run format:check --prefix frontend
make frontend-test
```

The phone controllers and desk markup are generated from AlgoDial's existing implementation. After editing the original desk sources run `.venv/bin/python scripts/sync_desk_frontend.py`, then rebuild. CI checks for drift. Do not edit generated desk files directly.

No SCC application configuration, credentials, workflows or external-service features are copied. Dependencies are pinned in `package-lock.json`; no runtime dependency on the reference checkout exists.
