/**
 * @jest-environment jsdom
 */

describe('createPopupContent', () => {
  const createPopupContent = (properties: Record<string, unknown>): string => {
    if (!properties) return '';

    const time = properties.time ? (() => {
      const raw = properties.time as string;
      const normalized = raw.trim().replace(' ', 'T');
      return `<span style="font-weight: bold; display: block; margin-bottom: 4px;">${normalized}</span>`;
    })() : '';

    const photoUrl = (() => {
      const url = properties.photo_url as string || '';
      if (!url) return '';
      const dangerousProtocols = /^(javascript:|data:|vbscript:|file:|about:)/i;
      if (dangerousProtocols.test(url)) return '';
      if (url.includes('..') || url.includes('%2e') || url.includes('%2f') || url.includes('%5c')) return '';
      return `<div style="margin-top: 8px;"><img src="${url}" style="width: auto; max-width: 100%; height: auto; max-height: 80vh; border-radius: 8px;" alt="Event photo"></div>`;
    })();

    const description = properties.description ? (() => {
      const escaped = (properties.description as string)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      return `<p>${escaped}</p>`;
    })() : '';

    return `<div class="photo-container" style="text-align: center; max-width: 360px; color: var(--tg-text-color, #000000); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
        ${time}
        ${photoUrl}
        ${description}
    </div>`;
  };

  test('returns empty string for null properties', () => {
    expect(createPopupContent(null as any)).toBe('');
  });

  test('returns empty string for undefined properties', () => {
    expect(createPopupContent(undefined as any)).toBe('');
  });

  test('renders valid properties with photo', () => {
    const props = {
      time: '2024-01-01T12:00:00Z',
      description: 'Bus on the street',
      photo_url: '/media/events/photo123.jpg',
    };
    const html = createPopupContent(props);
    expect(html).toContain('2024-01-01T12:00:00Z');
    expect(html).toContain('Bus on the street');
    expect(html).toContain('/media/events/photo123.jpg');
  });

  test('blocks javascript: in photo_url', () => {
    const props = {
      time: '2024-01-01T12:00:00Z',
      description: 'Test',
      photo_url: 'javascript:alert(1)',
    };
    const html = createPopupContent(props);
    expect(html).not.toContain('javascript:alert(1)');
  });

  test('blocks data: in photo_url', () => {
    const props = {
      time: '2024-01-01T12:00:00Z',
      description: 'Test',
      photo_url: 'data:text/html,<script>alert(1)</script>',
    };
    const html = createPopupContent(props);
    expect(html).not.toContain('data:text/html');
  });

  test('blocks path traversal in photo_url', () => {
    const props = {
      time: '2024-01-01T12:00:00Z',
      description: 'Test',
      photo_url: '/media/events/../../../etc/passwd',
    };
    const html = createPopupContent(props);
    expect(html).not.toContain('../../../etc/passwd');
  });

  test('escapes HTML in description', () => {
    const props = {
      time: '2024-01-01T12:00:00Z',
      description: '<script>alert(1)</script>',
    };
    const html = createPopupContent(props);
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
  });

  test('handles missing description', () => {
    const props = {
      time: '2024-01-01T12:00:00Z',
      photo_url: '/media/events/photo.jpg',
    };
    const html = createPopupContent(props);
    expect(html).toContain('2024-01-01T12:00:00Z');
    expect(html).toContain('/media/events/photo.jpg');
  });
});
