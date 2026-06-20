// api.js — HTTP primitives shared by every module.

// Build a URL with query params; arrays append repeated keys, blanks are skipped.
export function apiUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.forEach((item) => item && url.searchParams.append(key, item));
    } else if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  return url;
}

// Fetch wrapper that parses JSON and throws a readable error on non-2xx.
export async function request(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    const detail = body.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message || `Request failed with ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return body;
}
