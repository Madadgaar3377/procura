/** Progressive enhancement only: native markup stays visible without JS/observers. */
export function mountHomeMotion(root: HTMLElement) {
  const preference = window.matchMedia('(prefers-reduced-motion: reduce)');
  let dispose = () => {};

  function install() {
    const animations = new Set<Animation>();
    const listeners: (() => void)[] = [];
    const closing = new Map<HTMLDetailsElement, Animation>();
    const track = (node: Element, frames: Keyframe[], options: KeyframeAnimationOptions) => {
      const animation = node.animate(frames, options);
      animations.add(animation);
      const release = () => animations.delete(animation);
      animation.addEventListener('finish', release, {once: true});
      animation.addEventListener('cancel', release, {once: true});
      return animation;
    };

    // Observe once, ahead of the viewport. Never set opacity:0 in CSS or rely
    // on an observer firing to make server-rendered content readable.
    let observer: IntersectionObserver | undefined;
    if (typeof window.IntersectionObserver === 'function' && 'animate' in Element.prototype) {
      const order = new Map<Element, number>();
      const delays = new WeakMap<Element, number>();
      observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          observer?.unobserve(entry.target);
          const cell = entry.target.matches('td, th');
          track(entry.target, [
            {opacity: 0, ...(cell ? {} : {translate: '0 16px'})},
            {opacity: 1, ...(cell ? {} : {translate: '0 0'})},
          ], {duration: 460, delay: delays.get(entry.target) || 0,
            easing: 'cubic-bezier(.2,.65,.3,1)', fill: 'backwards'});
          entry.target.classList.add('pm-motion-seen');
        }
      }, {rootMargin: '0px 0px 48px 0px', threshold: 0});
      const targets = root.querySelectorAll([
        '.pm-reveal', '.pm-value-grid > div', '.pm-directory-section .pm-section-heading-row',
        '.pm-directory-card', '.pm-membership-section .pm-section-intro', '.pm-plan-card',
        '.pm-trust-grid > div', '.pm-six-stats > div', '.pm-dashboard-invitation',
        '.pm-matching-flow > div', '.pm-supplier-steps > li', '.pm-faq-list details',
        '.pm-comparison-scroll thead th:not(:first-child)', '.pm-comparison-scroll tbody td',
      ].join(','));
      for (const node of targets) {
        const parent = node.parentElement!;
        const index = order.get(parent) || 0;
        order.set(parent, index + 1);
        delays.set(node, Math.min(index, 5) * 80);
        // Above-the-fold content is already visible: avoid hiding it after paint.
        if (node.getBoundingClientRect().top >= window.innerHeight) observer.observe(node);
      }
    }

    // One delegated pointer listener, one frame at a time, no React renders.
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine) and (min-width: 1024px)');
    let tilted: HTMLElement | null = null;
    let frame = 0;
    let point = {x: 0, y: 0};
    const resetTilt = () => {
      cancelAnimationFrame(frame); frame = 0;
      if (tilted) {
        tilted.style.removeProperty('--pm-tilt-x');
        tilted.style.removeProperty('--pm-tilt-y');
        tilted.removeAttribute('data-tilting');
        tilted = null;
      }
    };
    const pointer = (event: PointerEvent) => {
      if (!finePointer.matches || event.pointerType !== 'mouse') { resetTilt(); return; }
      const target = (event.target as Element).closest<HTMLElement>(
        '.pm-workflow-window, .pm-form-window, .pm-supplier-window, .pm-automation-grid > article, .pm-matching-node, .pm-comparison-window');
      if (target !== tilted) { resetTilt(); tilted = target; }
      if (!tilted) return;
      point = {x: event.clientX, y: event.clientY};
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!tilted) return;
        const rect = tilted.getBoundingClientRect();
        const clamp = (value: number) => Math.max(-1, Math.min(1, value));
        // Large comparison tables stay almost flat so prices remain easy to read.
        const strength = tilted.matches('.pm-comparison-window') ? 1 : 3;
        tilted.style.setProperty('--pm-tilt-x', `${-clamp((point.y-rect.top)/rect.height*2-1)*strength}deg`);
        tilted.style.setProperty('--pm-tilt-y', `${clamp((point.x-rect.left)/rect.width*2-1)*strength}deg`);
        tilted.dataset.tilting = 'true';
      });
    };
    root.addEventListener('pointermove', pointer, {passive: true});
    root.addEventListener('pointerleave', resetTilt);
    window.addEventListener('blur', resetTilt);
    finePointer.addEventListener('change', resetTilt);
    listeners.push(() => {
      resetTilt(); root.removeEventListener('pointermove', pointer);
      root.removeEventListener('pointerleave', resetTilt);
      window.removeEventListener('blur', resetTilt);
      finePointer.removeEventListener('change', resetTilt);
    });

    if ('animate' in Element.prototype) {
      root.querySelectorAll<HTMLDetailsElement>('.pm-faq-list details').forEach(details => {
        const summary = details.querySelector('summary');
        const answer = details.querySelector<HTMLElement>(':scope > div');
        if (!summary || !answer) return;
        const click = (event: MouseEvent) => {
          if (!details.open) return; // Native opening, focus and keyboard behavior.
          event.preventDefault();
          const previous = closing.get(details);
          if (previous) { previous.cancel(); closing.delete(details); return; }
          const animation = track(answer, [{opacity: 1, translate: '0 0'}, {opacity: 0, translate: '0 -4px'}],
            {duration: 160, easing: 'ease-out'});
          closing.set(details, animation);
          animation.onfinish = () => { closing.delete(details); details.open = false; };
        };
        const toggle = () => {
          if (details.open && !closing.has(details)) track(answer,
            [{opacity: 0, translate: '0 -6px'}, {opacity: 1, translate: '0 0'}],
            {duration: 200, easing: 'ease-out'});
        };
        summary.addEventListener('click', click);
        details.addEventListener('toggle', toggle);
        listeners.push(() => { summary.removeEventListener('click', click); details.removeEventListener('toggle', toggle); });
      });
    }

    root.dataset.motion = 'enhanced';
    return () => {
      observer?.disconnect();
      listeners.forEach(remove => remove());
      closing.forEach((_, details) => { details.open = false; });
      animations.forEach(animation => animation.cancel());
      root.removeAttribute('data-motion');
    };
  }

  const update = () => { dispose(); dispose = preference.matches ? () => {} : install(); };
  update();
  preference.addEventListener('change', update);
  return () => { preference.removeEventListener('change', update); dispose(); };
}
