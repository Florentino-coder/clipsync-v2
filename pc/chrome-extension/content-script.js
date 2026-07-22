/**
 * Content script stub (Task 4.1).
 * Real text-anchor engine lands in Task 4.3 — here we only gate on domain_patterns.
 */

(function () {
  function urlMatchesPattern(url, pattern) {
    // Chrome match-pattern-ish: treat trailing * as prefix match on the rest.
    if (typeof pattern !== 'string' || !pattern) return false;
    if (pattern.endsWith('*')) {
      return url.startsWith(pattern.slice(0, -1));
    }
    return url === pattern;
  }

  function activeOnThisPage(profiles) {
    const href = location.href;
    return (profiles || []).some((p) =>
      (p.domain_patterns || []).some((pat) => urlMatchesPattern(href, pat))
    );
  }

  chrome.storage.local.get(['siteProfiles'], ({ siteProfiles }) => {
    if (!activeOnThisPage(siteProfiles)) {
      return; // wrong domain — do nothing
    }
    // Task 4.3: engine + dry_run outline + canary
  });
})();
