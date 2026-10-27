(() => {
  if (window.__autotestObserverInstalled) return;
  window.__autotestObserverInstalled = true;

  const SENSITIVE_RE =
    /(password|passwd|secret|token|api[_-]?key|authorization|cookie|session[_-]?id|credit[_-]?card|card[_-]?number|private[_-]?key|(?:^|[^a-z0-9])(?:pwd|auth|cvv|cvc|ssn|pin|otp)(?:$|[^a-z0-9]))/i;
  const MEANINGFUL_KEYS = new Set([
    "Enter",
    "Escape",
    "Tab",
    "Backspace",
    "Delete",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
  ]);
  const INTERACTIVE_SELECTOR =
    'a, button, input, select, textarea, summary, [role="button"], [role="link"], [role="menuitem"], [aria-haspopup], [tabindex]';
  const NOTABLE_MUTATION_SELECTOR =
    'h1, h2, h3, [role="alert"], [role="status"], [role="dialog"], dialog, .toast, .alert, .error, .modal';

  function send(eventType, target, payload) {
    try {
      if (typeof window.__autotestRecordEvent !== "function") return;
      window.__autotestRecordEvent({
        event_type: eventType,
        target: target || {},
        payload: payload || {},
        frame_url: window.location ? window.location.href : "",
        is_main_frame: window === window.top,
      });
    } catch (_) {
      // Never interfere with page execution.
    }
  }

  function isSensitiveElement(el) {
    if (!el || el.nodeType !== 1) return false;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (type === "password") return true;
    const autocomplete = (el.getAttribute("autocomplete") || "").toLowerCase();
    if (autocomplete.includes("password") || autocomplete.includes("cc-")) return true;
    const name = el.getAttribute("name") || "";
    const id = el.getAttribute("id") || "";
    const ariaLabel = el.getAttribute("aria-label") || "";
    const placeholder = el.getAttribute("placeholder") || "";
    return (
      SENSITIVE_RE.test(name) ||
      SENSITIVE_RE.test(id) ||
      SENSITIVE_RE.test(ariaLabel) ||
      SENSITIVE_RE.test(placeholder)
    );
  }

  function cleanText(raw, maxLen = 120) {
    if (!raw) return "";
    const normalized = String(raw).replace(/\s+/g, " ").trim();
    return normalized.length > maxLen ? normalized.slice(0, maxLen) : normalized;
  }

  function buildCssPath(el) {
    const parts = [];
    let current = el;
    let depth = 0;
    while (current && current.nodeType === 1 && depth < 5) {
      const tag = current.tagName.toLowerCase();
      if (current.id) {
        parts.unshift(`#${CSS.escape(current.id)}`);
        break;
      }
      let matchedTestAttr = "";
      let matchedTestId = "";
      for (const attr of ["data-testid", "data-test-id", "data-cy"]) {
        const val = current.getAttribute(attr);
        if (val) {
          matchedTestAttr = attr;
          matchedTestId = val;
          break;
        }
      }
      if (matchedTestId) {
        parts.unshift(`${tag}[${matchedTestAttr}="${matchedTestId.replace(/"/g, '\\"')}"]`);
        break;
      }
      let selector = tag;
      if (current.parentElement) {
        const siblings = Array.from(current.parentElement.children).filter(
          (child) => child.tagName === current.tagName
        );
        if (siblings.length > 1) {
          selector += `:nth-of-type(${siblings.indexOf(current) + 1})`;
        }
      }
      parts.unshift(selector);
      current = current.parentElement;
      depth += 1;
    }
    return parts.join(" > ");
  }

  function resolveElement(event, preferInteractive = false) {
    const path = typeof event.composedPath === "function" ? event.composedPath() : [];
    let el = path.find((node) => node && node.nodeType === 1) || event.target;
    if (el && el.nodeType !== 1) el = el.parentElement;
    if (!el || el.nodeType !== 1) return null;
    if (preferInteractive && typeof el.closest === "function") {
      const interactive = el.closest(INTERACTIVE_SELECTOR);
      if (interactive) return interactive;
    }
    return el;
  }

  function extractTarget(el) {
    if (!el || el.nodeType !== 1) return {};
    const sensitive = isSensitiveElement(el);
    const tag = el.tagName.toLowerCase();
    const target = {
      tag,
      css_path: buildCssPath(el),
    };
    let testIdAttr = "";
    let testIdVal = "";
    for (const attr of ["data-testid", "data-test-id", "data-cy"]) {
      const val = el.getAttribute(attr);
      if (val) {
        testIdAttr = attr;
        testIdVal = val;
        break;
      }
    }
    const attrs = {
      id: el.id || "",
      name: el.getAttribute("name") || "",
      role: el.getAttribute("role") || "",
      aria_label: el.getAttribute("aria-label") || "",
      placeholder: el.getAttribute("placeholder") || "",
      input_type: el.getAttribute("type") || "",
      autocomplete: el.getAttribute("autocomplete") || "",
      test_id: testIdVal,
      test_id_attr: testIdAttr,
      href: el.getAttribute("href") || "",
    };
    for (const [k, v] of Object.entries(attrs)) {
      if (v) target[k] = v;
    }
    if (!sensitive && tag !== "input" && tag !== "textarea") {
      const text = cleanText(el.innerText || el.textContent || "");
      if (text) target.text = text;
    }
    return target;
  }

  // --- Debounced input tracking ---
  let pendingInput = null;
  let pendingInputTimer = null;

  function flushPendingInput() {
    if (pendingInputTimer !== null) {
      clearTimeout(pendingInputTimer);
      pendingInputTimer = null;
    }
    if (!pendingInput) return;
    const item = pendingInput;
    pendingInput = null;
    send("input", item.target, item.payload);
  }

  document.addEventListener(
    "input",
    (event) => {
      const el = resolveElement(event, false);
      if (!el) return;
      const sensitive = isSensitiveElement(el);
      const rawValue = typeof el.value === "string" ? el.value : cleanText(el.textContent || "");
      const payload = {
        value: sensitive ? "[REDACTED]" : rawValue,
        redacted: sensitive,
        is_trusted: Boolean(event.isTrusted),
      };
      const target = extractTarget(el);
      if (pendingInput && pendingInput.el !== el) {
        flushPendingInput();
      }
      pendingInput = { el, target, payload };
      if (pendingInputTimer !== null) clearTimeout(pendingInputTimer);
      pendingInputTimer = setTimeout(flushPendingInput, 250);
    },
    true
  );

  // --- Click ---
  document.addEventListener(
    "click",
    (event) => {
      flushPendingInput();
      const el = resolveElement(event, true);
      if (!el) return;
      send("click", extractTarget(el), {
        button: event.button,
        x: Math.round(event.clientX),
        y: Math.round(event.clientY),
        is_trusted: Boolean(event.isTrusted),
      });
    },
    true
  );

  // --- Change ---
  document.addEventListener(
    "change",
    (event) => {
      flushPendingInput();
      const el = resolveElement(event, false);
      if (!el) return;
      const sensitive = isSensitiveElement(el);
      const tag = el.tagName.toLowerCase();
      const payload = {
        is_trusted: Boolean(event.isTrusted),
        redacted: sensitive,
      };
      if (el.type === "checkbox" || el.type === "radio") {
        payload.checked = Boolean(el.checked);
        payload.value = sensitive ? "[REDACTED]" : String(el.value || "");
      } else if (tag === "select") {
        payload.value = String(el.value || "");
        const selected = el.options && el.options[el.selectedIndex];
        if (selected) payload.selected_text = cleanText(selected.text);
      } else {
        payload.value = sensitive ? "[REDACTED]" : String(el.value || "");
      }
      send("change", extractTarget(el), payload);
    },
    true
  );

  // --- Submit ---
  document.addEventListener(
    "submit",
    (event) => {
      flushPendingInput();
      const el = resolveElement(event, false);
      if (!el) return;
      send("submit", extractTarget(el), {
        action: el.getAttribute("action") || "",
        method: (el.getAttribute("method") || "get").toLowerCase(),
        is_trusted: Boolean(event.isTrusted),
      });
    },
    true
  );

  // --- Meaningful keyboard events ---
  document.addEventListener(
    "keydown",
    (event) => {
      const hasModifier = event.ctrlKey || event.metaKey || event.altKey;
      if (!MEANINGFUL_KEYS.has(event.key) && !hasModifier) return;
      const el = resolveElement(event, false);
      if (isSensitiveElement(el) && !MEANINGFUL_KEYS.has(event.key)) return;
      if (event.key === "Enter" || event.key === "Tab") {
        flushPendingInput();
      }
      send("keydown", extractTarget(el), {
        key: event.key,
        ctrl: Boolean(event.ctrlKey),
        alt: Boolean(event.altKey),
        shift: Boolean(event.shiftKey),
        meta: Boolean(event.metaKey),
        is_trusted: Boolean(event.isTrusted),
      });
    },
    true
  );

  // --- Meaningful hover transitions (dwell >= 250ms on interactive elements) ---
  let hoverTimer = null;
  let lastHovered = null;

  document.addEventListener(
    "mouseover",
    (event) => {
      const el = resolveElement(event, true);
      if (!el || el === lastHovered) return;
      if (!el.matches || !el.matches(INTERACTIVE_SELECTOR)) return;
      if (hoverTimer !== null) clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => {
        lastHovered = el;
        send("hover", extractTarget(el), { is_trusted: Boolean(event.isTrusted) });
      }, 250);
    },
    true
  );

  document.addEventListener(
    "mouseout",
    (event) => {
      if (hoverTimer !== null) {
        clearTimeout(hoverTimer);
        hoverTimer = null;
      }
      if (event.target === lastHovered) {
        lastHovered = null;
      }
    },
    true
  );

  // --- Debounced scroll ---
  let scrollTimer = null;
  window.addEventListener(
    "scroll",
    () => {
      if (scrollTimer !== null) clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        send(
          "scroll",
          { tag: "window" },
          {
            scroll_x: Math.round(window.scrollX || 0),
            scroll_y: Math.round(window.scrollY || 0),
          }
        );
      }, 300);
    },
    { passive: true, capture: true }
  );

  // --- Debounced / grouped DOM mutations ---
  let mutationTimer = null;
  let mutationSummary = {
    added_count: 0,
    removed_count: 0,
    attribute_changes: 0,
    notable: [],
  };

  function collectNotable(node) {
    if (!node || node.nodeType !== 1 || mutationSummary.notable.length >= 5) return;
    const isNotable =
      (node.matches && node.matches(NOTABLE_MUTATION_SELECTOR)) ||
      (node.querySelector && node.querySelector(NOTABLE_MUTATION_SELECTOR));
    const el =
      node.matches && node.matches(NOTABLE_MUTATION_SELECTOR)
        ? node
        : isNotable
          ? node.querySelector(NOTABLE_MUTATION_SELECTOR)
          : node;
    const text = cleanText(el.innerText || el.textContent || "", 100);
    if (!text) return;
    mutationSummary.notable.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role") || "",
      id: el.id || "",
      text,
    });
  }

  function flushMutations() {
    mutationTimer = null;
    if (
      mutationSummary.added_count === 0 &&
      mutationSummary.removed_count === 0 &&
      mutationSummary.attribute_changes === 0
    ) {
      return;
    }
    const payload = mutationSummary;
    mutationSummary = {
      added_count: 0,
      removed_count: 0,
      attribute_changes: 0,
      notable: [],
    };
    send("dom_mutation", { tag: "document" }, payload);
  }

  function startMutationObserver() {
    if (!document.documentElement) return;
    const observer = new MutationObserver((records) => {
      for (const rec of records) {
        if (rec.type === "childList") {
          mutationSummary.added_count += rec.addedNodes.length;
          mutationSummary.removed_count += rec.removedNodes.length;
          for (const added of rec.addedNodes) {
            collectNotable(added);
          }
        } else if (rec.type === "attributes") {
          mutationSummary.attribute_changes += 1;
        }
      }
      if (mutationTimer === null) {
        mutationTimer = setTimeout(flushMutations, 200);
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "hidden", "aria-expanded", "aria-hidden", "disabled", "open"],
    });
  }

  if (document.documentElement) {
    startMutationObserver();
  } else {
    document.addEventListener("DOMContentLoaded", startMutationObserver, { once: true });
  }
})();
