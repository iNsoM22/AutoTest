# AutoTest Sample Demo App

A zero-dependency local web app (`index.html`) designed to exercise AutoTest's full workflow:

- **Login form** with a `type="password"` input (demonstrating automatic sensitive-data redaction and `os.environ.get(...)` code generation).
- **Task creation & completion** with `data-testid` and accessible `aria-label` attributes (demonstrating ranked selector extraction and assertions).
- **Live search filter** (demonstrating input debouncing).

## Quick Start

1. Serve the folder locally (or open `index.html` directly via `file://`):
   ```bash
   python -m http.server 8765 --directory examples/sample_demo_app
   ```
2. Launch AutoTest:
   ```bash
   uv run autotest
   ```
3. Create a project with Start URL `http://localhost:8765`, click **Start Recording**, sign in, add a task, check it off, and click **Stop Recording**.
4. Switch to **Timeline & Scenarios** -> **Test Definitions & Code** -> **Test Execution** to review, copy, and run the generated Playwright + Pytest tests.
