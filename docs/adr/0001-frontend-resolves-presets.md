# Presets are resolved by the frontend

The backend defines, versions, and serves the preset catalogue, but resolving a preset into raw parameters happens in the frontend: selecting a preset pre-fills the advanced tab's raw values, which the user may adjust before submitting. A job request therefore always carries raw parameters; only a submission from the simplified preset tab also carries the preset reference (id + version), and whatever was carried is recorded with the job. This reverses the earlier charting decision that the backend resolves presets — the UI model (preset selection and advanced tab are two views of the same raw values) makes client-side resolution the natural fit — and it keeps the API to a single request shape the backend validates uniformly.

## Considered Options

- Backend resolves: request carries either a preset reference or raw parameters. Rejected — two request shapes, and the advanced tab needs the resolved values client-side anyway.
- Frontend resolves (chosen): one request shape, catalogue served by the backend (e.g. on page load), preset reference logged for attribution only when the job was submitted as an unmodified preset.
