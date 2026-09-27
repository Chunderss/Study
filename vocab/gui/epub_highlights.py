"""DOM text anchors and passage highlighting without modifying EPUB files.

Anchor offsets use JavaScript's UTF-16 units, matching DOM Range offsets. Both
capture and restoration index the same rendered body text nodes. Passage text
is always passed as JSON or inserted as a text node, never parsed as HTML.
"""
import json


_HELPERS = r"""
const STATE_KEY = '__vocabStudyHighlights';
const SAVED = 'vocab-study-saved';
const ACTIVE = 'vocab-study-active';

function textIndex() {
    const entries = [];
    const chunks = [];
    let length = 0;
    if (!document.body) return {entries, text: ''};
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const probe = document.createRange();
    let node;
    while ((node = walker.nextNode())) {
        if (!node.length || !node.parentElement ||
            node.parentElement.closest('script,style,noscript,template,[hidden],textarea,select'))
            continue;
        const style = getComputedStyle(node.parentElement);
        if (style.visibility === 'hidden' || style.visibility === 'collapse') continue;
        probe.selectNodeContents(node);
        if (!probe.getClientRects().length) continue;
        entries.push({node, start: length, end: length + node.length});
        chunks.push(node.data);
        length += node.length;
    }
    return {entries, text: chunks.join('')};
}

function pointOffset(index, container, offset) {
    const probe = document.createRange();
    for (const entry of index.entries) {
        if (entry.node === container) return entry.start + offset;
        probe.selectNodeContents(entry.node);
        if (probe.comparePoint(container, offset) < 0) return entry.start;
    }
    return index.text.length;
}

function contextBefore(text, position) {
    let start = Math.max(0, position - 64);
    // Never persist a lone surrogate at the boundary of an emoji context.
    if (start && /[\uDC00-\uDFFF]/.test(text[start]) &&
        /[\uD800-\uDBFF]/.test(text[start - 1])) --start;
    return text.slice(start, position);
}

function contextAfter(text, position) {
    let end = Math.min(text.length, position + 64);
    if (end < text.length && /[\uD800-\uDBFF]/.test(text[end - 1]) &&
        /[\uDC00-\uDFFF]/.test(text[end])) ++end;
    return text.slice(position, end);
}

function selectedAnchor(index) {
    const selection = getSelection();
    if (!selection || !selection.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    if (!document.body.contains(range.startContainer) ||
        !document.body.contains(range.endContainer)) return null;
    const start = pointOffset(index, range.startContainer, range.startOffset);
    const end = pointOffset(index, range.endContainer, range.endOffset);
    const exact = index.text.slice(start, end);
    if (!exact.trim()) return null;
    return {
        anchor: {version: 1, exact, prefix: contextBefore(index.text, start),
                 suffix: contextAfter(index.text, end), start, end},
        backward: pointOffset(index, selection.anchorNode, selection.anchorOffset) >
                  pointOffset(index, selection.focusNode, selection.focusOffset)
    };
}

function resolveAnchor(index, anchor) {
    if (!anchor || (anchor.version !== undefined && anchor.version !== 1) ||
        typeof anchor.exact !== 'string' || !anchor.exact.length) return null;
    const text = index.text, exact = anchor.exact;
    const prefix = typeof anchor.prefix === 'string' ? anchor.prefix : '';
    const suffix = typeof anchor.suffix === 'string' ? anchor.suffix : '';
    const hasContext = !!(prefix || suffix);
    const contextMatches = start =>
        (!prefix || text.slice(Math.max(0, start - prefix.length), start) === prefix) &&
        (!suffix || text.slice(start + exact.length, start + exact.length + suffix.length) === suffix);
    const knownOffset = Number.isInteger(anchor.start) && Number.isInteger(anchor.end) &&
                        anchor.start >= 0 && anchor.end - anchor.start === exact.length;
    if (knownOffset && text.slice(anchor.start, anchor.end) === exact &&
        contextMatches(anchor.start)) return {start: anchor.start, end: anchor.end};
    const matches = [];
    let position = text.indexOf(exact);
    while (position !== -1) {
        matches.push(position);
        // An ambiguous single-letter legacy capture should not scan forever.
        if (matches.length > 10000) return null;
        position = text.indexOf(exact, position + 1);
    }
    if (matches.length === 1) return {start: matches[0], end: matches[0] + exact.length};
    if (!hasContext) return null;
    const contextual = matches.filter(contextMatches);
    if (contextual.length === 1)
        return {start: contextual[0], end: contextual[0] + exact.length};
    // Exact-only legacy captures with repeated text are deliberately unresolved.
    return null;
}

function domRange(index, start, end) {
    const first = index.entries.find(entry => start >= entry.start && start < entry.end);
    const last = index.entries.find(entry => end > entry.start && end <= entry.end);
    if (!first || !last || start >= end) return null;
    const range = document.createRange();
    range.setStart(first.node, start - first.start);
    range.setEnd(last.node, end - last.start);
    return range;
}

function clearHighlights(state) {
    if (state.registry) {
        state.registry.delete(SAVED);
        state.registry.delete(ACTIVE);
    }
    const parents = new Set();
    for (const span of state.spans || []) {
        if (!span.parentNode) continue;
        parents.add(span.parentNode);
        span.replaceWith(...span.childNodes);
    }
    for (const parent of parents) parent.normalize();
    state.spans = [];
}

function spanHighlights(index, resolved, activeId, state) {
    for (const entry of index.entries) {
        const touching = resolved.filter(item => item.start < entry.end && item.end > entry.start);
        if (!touching.length) continue;
        const cuts = new Set([0, entry.node.length]);
        for (const item of touching) {
            cuts.add(Math.max(0, item.start - entry.start));
            cuts.add(Math.min(entry.node.length, item.end - entry.start));
        }
        const ordered = [...cuts].sort((a, b) => a - b);
        const fragment = document.createDocumentFragment();
        for (let i = 1; i < ordered.length; ++i) {
            const start = ordered[i - 1], end = ordered[i];
            const covering = touching.filter(item => item.start < entry.start + end &&
                                                       item.end > entry.start + start);
            const text = document.createTextNode(entry.node.data.slice(start, end));
            if (!covering.length) {
                fragment.appendChild(text);
                continue;
            }
            const span = document.createElement('span');
            const active = covering.some(item => item.id === activeId);
            span.setAttribute('data-vocab-study-highlight', covering.map(item => item.id).join(' '));
            if (active) span.setAttribute('data-vocab-study-active', 'true');
            span.style.cssText = 'all:unset;background-color:' +
                (active ? 'rgba(242,157,54,0.72)' : 'rgba(244,204,69,0.45)') +
                ';color:inherit;' + (active ? 'text-decoration:underline;' : '');
            span.appendChild(text);
            fragment.appendChild(span);
            state.spans.push(span);
        }
        entry.node.replaceWith(fragment);
    }
}

function restoreSelection(index, saved) {
    if (!saved) return;
    const location = resolveAnchor(index, saved.anchor);
    const range = location && domRange(index, location.start, location.end);
    if (!range) return;
    const selection = getSelection();
    selection.removeAllRanges();
    if (saved.backward && selection.setBaseAndExtent)
        selection.setBaseAndExtent(range.endContainer, range.endOffset,
                                   range.startContainer, range.startOffset);
    else selection.addRange(range);
}
"""


SELECTION_ANCHOR_JS = "(() => {\n" + _HELPERS + r"""
    const selected = selectedAnchor(textIndex());
    if (!selected) return '';
    const root = document.scrollingElement || document.documentElement;
    return JSON.stringify({...selected.anchor,
        quote: getSelection().toString().trim(),
        scroll: Math.max(0, Math.min(1, window.scrollY / Math.max(1, root.scrollHeight)))});
})();
"""


def render_highlights_js(records, active_id=None, focus_id=None):
    """Build a script for ``[{id, anchor}]``; return a JSON rendering report.

    ``anchor={'exact': quote}`` supports unambiguous legacy captures. Full
    anchors also disambiguate repeated text. ``focus_id`` scrolls to a resolved
    passage; the report's ``focused`` flag tells the reader whether that worked.
    """
    payload = json.dumps({"records": records, "activeId": active_id, "focusId": focus_id})
    return "(() => {\n" + _HELPERS + "\nconst payload = " + payload + r""";
    const state = window[STATE_KEY] || (window[STATE_KEY] = {spans: []});
    const nativeSupported = !!(window.CSS && CSS.highlights && typeof window.Highlight === 'function');
    const hadSpans = state.spans.some(span => span.isConnected);
    const browserSelection = getSelection();
    let index = null, selection = null;
    // Native highlights do not mutate the DOM or disturb the selection.
    if ((hadSpans || (!nativeSupported && payload.records.length)) &&
        browserSelection && !browserSelection.isCollapsed) {
        index = textIndex();
        selection = selectedAnchor(index);
    }
    clearHighlights(state);
    if (hadSpans) index = null;
    const renderer = nativeSupported ? 'css' : 'spans';
    if (!payload.records.length) {
        if (selection) restoreSelection(textIndex(), selection);
        return JSON.stringify({supported: true, renderer,
            rendered: [], unresolved: [], focused: false});
    }
    index = index || textIndex();
    const resolved = [], unresolved = [];
    for (const record of payload.records) {
        const id = String(record.id);
        const location = resolveAnchor(index, record.anchor);
        if (location) resolved.push({id, ...location});
        else unresolved.push(id);
    }
    const activeId = payload.activeId === null ? payload.focusId : payload.activeId;
    if (nativeSupported) {
        const saved = new Highlight(), active = new Highlight();
        for (const item of resolved) {
            const range = domRange(index, item.start, item.end);
            if (!range) continue;
            saved.add(range);
            if (item.id === activeId) active.add(range);
        }
        active.priority = 1;
        state.registry = CSS.highlights;
        state.registry.set(SAVED, saved);
        state.registry.set(ACTIVE, active);
        if (!state.style || !state.style.isConnected) {
            state.style = document.createElement('style');
            state.style.textContent =
                '::highlight(vocab-study-saved){background-color:rgba(244,204,69,0.45);color:inherit}' +
                '::highlight(vocab-study-active){background-color:rgba(242,157,54,0.72);color:inherit;text-decoration:underline}';
            (document.head || document.body).appendChild(state.style);
        }
        if (selection) restoreSelection(index, selection);
    } else {
        spanHighlights(index, resolved, activeId, state);
        if (selection || payload.focusId !== null) index = textIndex();
        restoreSelection(index, selection);
    }
    let focused = false;
    const target = resolved.find(item => item.id === payload.focusId);
    const range = target && domRange(index, target.start, target.end);
    if (range && innerHeight > 0 && document.visibilityState !== 'hidden') {
        const rect = [...range.getClientRects()].find(rect => rect.height > 0);
        if (rect) {
            // EPUB styles may request smooth scrolling or mandatory snap points.
            // Report success only when the first passage line is actually visible.
            window.scrollTo({left: 0,
                top: window.scrollY + rect.top - Math.max(16, innerHeight * 0.25),
                behavior: 'instant'});
            const placed = [...range.getClientRects()].find(rect => rect.height > 0);
            focused = !!placed && placed.bottom > 0 && placed.top < innerHeight;
        }
    }
    return JSON.stringify({supported: true, renderer,
        rendered: resolved.map(item => item.id), unresolved, focused});
})();
"""
