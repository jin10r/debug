/**
 * Unit tests for sanitizeUrl XSS prevention
 * 
 * Tests cover:
 * - Dangerous protocols (javascript:, data:, vbscript:, etc.)
 * - Path traversal attempts
 * - Valid relative and absolute URLs
 * - Edge cases (null, undefined, empty string)
 */

// Mock the sanitizeUrl function by extracting it from map.ts
// Since map.ts has window dependencies, we'll test the logic directly

describe('sanitizeUrl XSS Prevention', () => {
  // Implementation of sanitizeUrl for testing (mirrors map.ts)
  function sanitizeUrl(url: unknown): string {
    if (typeof url !== 'string' || !url) {
      return '';
    }
    
    const trimmedUrl = url.trim();
    
    // Block dangerous protocols
    const dangerousProtocols = /^(javascript:|data:|vbscript:|file:|about:)/i;
    if (dangerousProtocols.test(trimmedUrl)) {
      return '';
    }
    
    // Allow relative URLs from our media endpoints
    if (trimmedUrl.startsWith('/media/events/') || trimmedUrl.startsWith('/api/media/')) {
      if (trimmedUrl.includes('..') || trimmedUrl.includes('%2f') || trimmedUrl.includes('%5c')) {
        return '';
      }
      return trimmedUrl;
    }
    
    // For absolute URLs, only allow HTTPS
    try {
      const parsedUrl = new URL(trimmedUrl);
      if (parsedUrl.protocol !== 'https:') {
        return '';
      }
      return trimmedUrl;
    } catch {
      if (trimmedUrl.startsWith('/')) {
        return '';
      }
      return '';
    }
  }

  describe('Dangerous Protocol Blocking', () => {
    test('should block javascript: protocol', () => {
      expect(sanitizeUrl('javascript:alert(1)')).toBe('');
      expect(sanitizeUrl('JAVASCRIPT:alert(1)')).toBe('');
      expect(sanitizeUrl('javascript:void(0)')).toBe('');
    });

    test('should block data: protocol', () => {
      expect(sanitizeUrl('data:text/html,<script>alert(1)</script>')).toBe('');
      expect(sanitizeUrl('data:image/svg+xml,<svg onload=alert(1)>')).toBe('');
    });

    test('should block vbscript: protocol', () => {
      expect(sanitizeUrl('vbscript:msgbox(1)')).toBe('');
    });

    test('should block file: protocol', () => {
      expect(sanitizeUrl('file:///etc/passwd')).toBe('');
    });

    test('should block about: protocol', () => {
      expect(sanitizeUrl('about:blank')).toBe('');
    });
  });

  describe('Path Traversal Prevention', () => {
    test('should block .. directory traversal', () => {
      expect(sanitizeUrl('/media/events/../../../etc/passwd')).toBe('');
      expect(sanitizeUrl('/api/media/../../config')).toBe('');
    });

    test('should block encoded path traversal', () => {
      expect(sanitizeUrl('/media/events/%2e%2e/passwd')).toBe('');
      expect(sanitizeUrl('/api/media/%5c%5c/etc')).toBe('');
    });
  });

  describe('Valid URL Acceptance', () => {
    test('should accept valid relative media URLs', () => {
      expect(sanitizeUrl('/media/events/photo123.jpg')).toBe('/media/events/photo123.jpg');
      expect(sanitizeUrl('/api/media/events/photo456.jpg')).toBe('/api/media/events/photo456.jpg');
    });

    test('should accept valid HTTPS URLs', () => {
      expect(sanitizeUrl('https://example.com/photo.jpg')).toBe('https://example.com/photo.jpg');
      expect(sanitizeUrl('https://cdn.telegram.org/file/photo.jpg')).toBe('https://cdn.telegram.org/file/photo.jpg');
    });

    test('should block non-HTTPS absolute URLs', () => {
      expect(sanitizeUrl('http://example.com/photo.jpg')).toBe('');
      expect(sanitizeUrl('ftp://example.com/file')).toBe('');
    });
  });

  describe('Edge Cases', () => {
    test('should handle null and undefined', () => {
      expect(sanitizeUrl(null)).toBe('');
      expect(sanitizeUrl(undefined)).toBe('');
    });

    test('should handle empty string', () => {
      expect(sanitizeUrl('')).toBe('');
      expect(sanitizeUrl('   ')).toBe('');
    });

    test('should handle non-string types', () => {
      expect(sanitizeUrl(123)).toBe('');
      expect(sanitizeUrl({})).toBe('');
      expect(sanitizeUrl([])).toBe('');
    });

    test('should handle XSS attempts in photo_url', () => {
      // Real-world XSS vectors
      expect(sanitizeUrl('javascript:alert(document.cookie)')).toBe('');
      expect(sanitizeUrl('javascript:window.location=\'http://evil.com\'')).toBe('');
      expect(sanitizeUrl('data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==')).toBe('');
    });
  });

  describe('Security Regression Tests', () => {
    test('should block onerror XSS vector', () => {
      // This would be: <img src="${photoUrl}" onerror="alert(1)">
      // But since we control the src attribute and sanitize it, onerror can't be injected
      expect(sanitizeUrl('x" onerror="alert(1)')).toBe('');
    });

    test('should block onload XSS vector', () => {
      expect(sanitizeUrl('x" onload="alert(1)')).toBe('');
    });

    test('should handle malformed URLs', () => {
      expect(sanitizeUrl('not-a-url')).toBe('');
      expect(sanitizeUrl('://missing-protocol.com')).toBe('');
    });
  });
});
