/*
 * Feedback Collector widget.
 *
 * Embed on any site:
 *   <script src="https://YOUR_SITE/widget.js" data-project="proj_..." data-api="https://YOUR_API/v1" async></script>
 *
 * Optional attributes:
 *   data-color="#1976d2"     Button and accent color (hex)
 *   data-position="right"    "right" or "left"
 *   data-label="Feedback"    Button text
 *   data-title="Send us feedback"
 *
 * JavaScript API (after the script loads):
 *   FeedbackWidget.open() / FeedbackWidget.close()
 *   FeedbackWidget.identify({ name, email })   Attach the signed-in user's details
 *
 * Keep this file pure ASCII (write symbols as \u{...} escapes): host pages may not
 * declare a charset, and then browsers decode the script with the wrong encoding.
 */
(function () {
    'use strict';

    // Loaded twice on the same page: keep the first one
    if (window.FeedbackWidget) return;

    const script = document.currentScript ||
        document.querySelector('script[data-project][src*="widget.js"]');
    if (!script) return;

    const projectId = script.dataset.project;
    const apiUrl = (script.dataset.api || '').replace(/\/+$/, '');
    if (!projectId || !apiUrl) {
        console.error('[FeedbackWidget] The script tag needs data-project and data-api attributes.');
        return;
    }

    // Only accept a hex color, so the attribute can't inject arbitrary CSS
    const color = /^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(script.dataset.color || '') ? script.dataset.color : '#1976d2';
    const side = script.dataset.position === 'left' ? 'left' : 'right';
    const label = script.dataset.label || 'Feedback';
    const title = script.dataset.title || 'Send us feedback';

    let identity = {};
    let isOpen = false;
    let closeTimer = null;

    const STYLES = `
        :host { all: initial; }
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }

        .launcher {
            position: fixed; bottom: 20px; ${side}: 20px; z-index: 2147483000;
            display: flex; align-items: center; gap: 8px;
            padding: 12px 18px; border: none; border-radius: 999px;
            background: ${color}; color: #fff; font-size: 15px; font-weight: 600;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25); cursor: pointer;
        }
        .launcher:hover { filter: brightness(0.92); }
        .launcher:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible,
        textarea:focus-visible { outline: 3px solid ${color}; outline-offset: 2px; }

        .panel {
            position: fixed; bottom: 80px; ${side}: 20px; z-index: 2147483001;
            width: 360px; max-width: calc(100vw - 32px); max-height: calc(100vh - 100px); overflow-y: auto;
            background: #fff; color: #222; border-radius: 14px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.25); padding: 20px;
        }
        .panel[hidden] { display: none; }

        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
        h2 { margin: 0; font-size: 18px; color: #222; }
        .close {
            border: none; background: none; font-size: 24px; line-height: 1; color: #666;
            cursor: pointer; padding: 4px 8px; border-radius: 6px;
        }
        .close:hover { background: #f0f0f0; }

        label, legend { display: block; font-size: 13px; font-weight: 600; color: #444; margin-bottom: 6px; }
        .field { margin-bottom: 14px; }
        fieldset { border: none; margin: 0 0 14px; padding: 0; }
        .optional { font-weight: 400; color: #888; }

        select, input[type="email"], textarea {
            width: 100%; padding: 10px; border: 1px solid #ccc; border-radius: 8px;
            font-size: 14px; color: #222; background: #fafafa;
        }
        textarea { min-height: 96px; resize: vertical; }

        /* Stars: inputs in reverse order so ~ highlights the chosen star and those to its left */
        .stars { display: flex; flex-direction: row-reverse; justify-content: flex-end; gap: 2px; }
        .stars input { position: absolute; width: 1px; height: 1px; opacity: 0; }
        .stars label { font-size: 26px; line-height: 1; color: #ccc; cursor: pointer; margin: 0; font-weight: 400; }
        .stars input:checked ~ label, .stars label:hover, .stars label:hover ~ label { color: #f5a623; }
        .stars input:focus-visible + label { outline: 2px solid ${color}; outline-offset: 2px; border-radius: 4px; }

        /* Spam trap: off-screen rather than display:none so bots still fill it in */
        .trap { position: absolute; left: -10000px; width: 1px; height: 1px; overflow: hidden; }

        .submit {
            width: 100%; padding: 12px; border: none; border-radius: 8px;
            background: ${color}; color: #fff; font-size: 15px; font-weight: 600; cursor: pointer;
        }
        .submit:disabled { opacity: 0.6; cursor: not-allowed; }

        .status { margin-top: 12px; font-size: 14px; padding: 10px; border-radius: 8px; }
        .status[hidden] { display: none; }
        .status.error { background: #fdecea; color: #8a1c1c; }

        .thanks { text-align: center; padding: 24px 8px; }
        .thanks[hidden] { display: none; }
        .thanks .check { font-size: 40px; color: #2e7d32; }
        .thanks p { font-size: 16px; color: #222; margin: 8px 0 0; }

        @media (max-width: 480px) {
            .panel { left: 16px; right: 16px; width: auto; max-width: none; bottom: 76px; }
        }
    `;

    const MARKUP = `
        <button class="launcher" type="button" aria-haspopup="dialog" aria-expanded="false">
            <span aria-hidden="true">\u{1F4AC}</span><span class="launcher-label"></span>
        </button>
        <div class="panel" role="dialog" aria-labelledby="fw-title" hidden>
            <div class="header">
                <h2 id="fw-title"></h2>
                <button class="close" type="button" aria-label="Close">\u{D7}</button>
            </div>
            <form novalidate>
                <div class="field">
                    <label for="fw-category">What is it about?</label>
                    <select id="fw-category" name="category">
                        <option value="general">General feedback</option>
                        <option value="bug">Something is broken</option>
                        <option value="feature">Feature request</option>
                        <option value="praise">Something I like</option>
                        <option value="other">Other</option>
                    </select>
                </div>
                <fieldset>
                    <legend>How is your experience? <span class="optional">(optional)</span></legend>
                    <div class="stars">
                        <input type="radio" id="fw-r5" name="rating" value="5"><label for="fw-r5" title="5 stars">\u{2605}</label>
                        <input type="radio" id="fw-r4" name="rating" value="4"><label for="fw-r4" title="4 stars">\u{2605}</label>
                        <input type="radio" id="fw-r3" name="rating" value="3"><label for="fw-r3" title="3 stars">\u{2605}</label>
                        <input type="radio" id="fw-r2" name="rating" value="2"><label for="fw-r2" title="2 stars">\u{2605}</label>
                        <input type="radio" id="fw-r1" name="rating" value="1"><label for="fw-r1" title="1 star">\u{2605}</label>
                    </div>
                </fieldset>
                <div class="field">
                    <label for="fw-message">Your feedback</label>
                    <textarea id="fw-message" name="message" maxlength="2000" required
                              placeholder="Tell us what you think..."></textarea>
                </div>
                <div class="field email-field">
                    <label for="fw-email">Email <span class="optional">(optional, if you'd like a reply)</span></label>
                    <input type="email" id="fw-email" name="email" maxlength="254" autocomplete="email">
                </div>
                <div class="trap" aria-hidden="true">
                    <label for="fw-website">Website</label>
                    <input type="text" id="fw-website" name="website" tabindex="-1" autocomplete="off">
                </div>
                <button class="submit" type="submit">Send feedback</button>
                <div class="status" role="alert" hidden></div>
            </form>
            <div class="thanks" hidden>
                <div class="check" aria-hidden="true">\u{2713}</div>
                <p>Thanks! Your feedback was sent.</p>
            </div>
        </div>
    `;

    function build() {
        // A custom tag so the page's `div { ... }` rules can't match the container, and an
        // inline !important reset as a backstop against broader selectors
        const host = document.createElement('feedback-collector-widget');
        host.id = 'feedback-collector-widget';
        host.setAttribute('style', 'all: initial !important;');
        const root = host.attachShadow({ mode: 'open' });
        root.innerHTML = `<style>${STYLES}</style>${MARKUP}`;
        document.body.appendChild(host);

        const $ = (selector) => root.querySelector(selector);
        const launcher = $('.launcher');
        const panel = $('.panel');
        const form = $('form');
        const status = $('.status');
        const thanks = $('.thanks');
        const submitButton = $('.submit');

        // Text set via textContent, so attribute values can't inject markup
        $('.launcher-label').textContent = label;
        $('#fw-title').textContent = title;

        function showError(message) {
            status.textContent = message;
            status.className = 'status error';
            status.hidden = false;
        }

        function open() {
            clearTimeout(closeTimer);
            isOpen = true;
            panel.hidden = false;
            launcher.setAttribute('aria-expanded', 'true');
            form.hidden = false;
            thanks.hidden = true;
            // The email field is pointless when the host page already told us who this is
            $('.email-field').hidden = Boolean(identity.email);
            $('#fw-message').focus();
        }

        function close() {
            clearTimeout(closeTimer);
            if (!isOpen) return;
            isOpen = false;
            panel.hidden = true;
            launcher.setAttribute('aria-expanded', 'false');
            launcher.focus();
        }

        launcher.addEventListener('click', () => (isOpen ? close() : open()));
        $('.close').addEventListener('click', close);
        root.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') close();
        });

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            status.hidden = true;

            const message = form.message.value.trim();
            if (!message) {
                showError('Please write your feedback first.');
                form.message.focus();
                return;
            }
            const email = identity.email || form.email.value.trim();
            if (email && !form.email.checkValidity() && !identity.email) {
                showError('Please enter a valid email address, or leave it empty.');
                form.email.focus();
                return;
            }

            const rating = form.querySelector('input[name="rating"]:checked');
            const body = {
                message,
                category: form.category.value,
                pageUrl: location.href.slice(0, 2000),
                website: form.website.value
            };
            if (rating) body.rating = Number(rating.value);
            if (email) body.email = email;
            if (identity.name) body.name = identity.name;

            submitButton.disabled = true;
            submitButton.textContent = 'Sending...';
            try {
                const response = await fetch(`${apiUrl}/projects/${encodeURIComponent(projectId)}/feedback`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    // Validation and origin errors are written for people; show them
                    throw new Error(response.status < 500 && data.message
                        ? data.message : 'Something went wrong. Please try again.');
                }
                form.reset();
                form.hidden = true;
                thanks.hidden = false;
                closeTimer = setTimeout(close, 2500);
            } catch (err) {
                showError(err.message === 'Failed to fetch'
                    ? 'Could not reach the server. Check your connection and try again.' : err.message);
            } finally {
                submitButton.disabled = false;
                submitButton.textContent = 'Send feedback';
            }
        });

        return { open, close };
    }

    let widget = null;
    const pending = [];

    function whenReady(action) {
        if (widget) action();
        else pending.push(action);
    }

    function init() {
        widget = build();
        pending.splice(0).forEach((action) => action());
    }

    window.FeedbackWidget = {
        open: () => whenReady(() => widget.open()),
        close: () => whenReady(() => widget.close()),
        identify: (details) => {
            details = details || {};
            identity = {
                name: typeof details.name === 'string' ? details.name.slice(0, 100) : undefined,
                email: typeof details.email === 'string' ? details.email.slice(0, 254) : undefined
            };
        }
    };

    if (document.body) {
        init();
    } else {
        document.addEventListener('DOMContentLoaded', init);
    }
})();
