/**
 * Fireflies Notetaker - Silent Auto-Admit Content Script
 * 
 * Automatically identifies and clicks "Admit" prompts in Google Meet
 * so the host never has to manually click "Admit" when the bot joins.
 */

(function () {
  console.log('[Fireflies Auto-Admit] Initializing silent auto-admission engine...');

  let admitCount = 0;

  function findAndClickAdmit() {
    const buttons = Array.from(document.querySelectorAll('button, div[role="button"]'));
    for (const btn of buttons) {
      const text = (btn.innerText || '').toLowerCase().trim();
      const aria = (btn.getAttribute('aria-label') || '').toLowerCase().trim();
      const title = (btn.getAttribute('title') || '').toLowerCase().trim();

      // Check if button is a Deny action
      const isDeny = text.includes('deny') || aria.includes('deny') || title.includes('deny');

      // Check if button is an "Admit" action
      const isAdmit = (
        text === 'admit' ||
        text.includes('admit 1 guest') ||
        text.includes('admit all') ||
        text.startsWith('admit') ||
        aria.includes('admit') ||
        title.includes('admit')
      );

      const rect = btn.getBoundingClientRect();
      const isVisible = rect.width > 0 && rect.height > 0;

      if (isAdmit && !isDeny && isVisible) {
        console.log('[Fireflies Auto-Admit] ⚡ Auto-admitting incoming participant/bot:', btn);
        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evtType => {
          try {
            btn.dispatchEvent(new MouseEvent(evtType, { bubbles: true, cancelable: true, view: window }));
          } catch (e) {}
        });
        btn.click();
        admitCount++;
        showSubtleBadge(`Auto-Admitted #${admitCount}`);
        return true;
      }
    }
    return false;
  }

  // Floating indicator badge on Google Meet
  function showSubtleBadge(text) {
    let badge = document.getElementById('fireflies-admit-badge');
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'fireflies-admit-badge';
      badge.style.position = 'fixed';
      badge.style.bottom = '16px';
      badge.style.left = '16px';
      badge.style.zIndex = '999999';
      badge.style.background = '#ea580c';
      badge.style.color = '#ffffff';
      badge.style.padding = '6px 12px';
      badge.style.borderRadius = '6px';
      badge.style.fontSize = '12px';
      badge.style.fontWeight = 'bold';
      badge.style.boxShadow = '0 4px 12px rgba(0,0,0,0.2)';
      badge.style.fontFamily = 'system-ui, sans-serif';
      badge.style.display = 'flex';
      badge.style.alignItems = 'center';
      badge.style.gap = '6px';
      badge.innerHTML = `<span>⚡</span> <span>${text}</span>`;
      document.body.appendChild(badge);
    } else {
      badge.innerHTML = `<span>⚡</span> <span>${text}</span>`;
      badge.style.display = 'flex';
    }

    setTimeout(() => {
      if (badge) badge.style.display = 'none';
    }, 4000);
  }

  // Run fast check on DOM mutations
  const observer = new MutationObserver(() => {
    findAndClickAdmit();
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  });

  // Also poll every 600ms as a reliable fallback
  setInterval(findAndClickAdmit, 600);

  console.log('[Fireflies Auto-Admit] Silent Auto-Admit is active and listening for entry requests.');
})();
