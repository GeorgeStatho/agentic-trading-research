import { useEffect, useState } from 'react';

/**
 * Which of a page's sections the reader is currently looking at.
 *
 * The sidebar highlight used to move only when an item was clicked, so it
 * disagreed with the page the moment the reader scrolled instead.
 */

/** Clears the sticky topbar, matching `scroll-margin-top` on a section. */
const TOPBAR_OFFSET_PX = 88;

/**
 * Only the upper part of the viewport decides: without this, the last section
 * on screen would win and the highlight would run ahead of the reader.
 */
const LOWER_VIEWPORT_MARGIN = '-55%';

/**
 * `sections` must be referentially stable (a module-level list, or one chosen
 * from a fixed set); the observer is rebuilt whenever its identity changes.
 */
export function useActiveSection(sections: readonly { id: string }[]): string | null {
  const [onScreenId, setOnScreenId] = useState<string | null>(null);

  useEffect(() => {
    const onScreen = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            onScreen.add(entry.target.id);
          } else {
            onScreen.delete(entry.target.id);
          }
        });

        const topmost = sections.find((section) => onScreen.has(section.id));
        if (topmost) {
          setOnScreenId(topmost.id);
        }
      },
      { rootMargin: `-${TOPBAR_OFFSET_PX}px 0px ${LOWER_VIEWPORT_MARGIN} 0px` },
    );

    sections.forEach((section) => {
      const element = document.getElementById(section.id);
      if (element) {
        observer.observe(element);
      }
    });

    return () => observer.disconnect();
  }, [sections]);

  // The remembered id belongs to whatever was on screen when it was read, so a
  // page that does not list it falls back to its own first section.
  const isCurrent = onScreenId !== null && sections.some((section) => section.id === onScreenId);
  return isCurrent ? onScreenId : sections[0]?.id ?? null;
}
