function syncPresetOrder(board) {
    const input = document.getElementById("preset-order-input");
    if (!input) {
        return;
    }

    const slots = Array.from(board.querySelectorAll(".preset-slot"));
    const order = [];
    slots.forEach((slot, index) => {
        const number = index + 1;
        slot.dataset.slot = String(number);
        const numberLabel = slot.querySelector(".preset-number");
        if (numberLabel) {
            numberLabel.textContent = String(number);
        }
        order.push(slot.dataset.presetId || "");
    });
    input.value = order.join(",");
}

function commitPresetOrderToRadioSlots(board) {
    if (!board) {
        return;
    }

    Array.from(board.querySelectorAll(".preset-slot")).forEach((slot, index) => {
        const slotNumber = String(index + 1);
        slot.dataset.slot = slotNumber;
        const numberLabel = slot.querySelector(".preset-number");
        if (numberLabel) {
            numberLabel.textContent = slotNumber;
        }

        if (slot.classList.contains("empty")) {
            slot.dataset.presetId = "";
            return;
        }

        slot.dataset.presetId = slotNumber;
        const contentIdInput = slot.querySelector('input[name="content_item_id"]');
        if (contentIdInput) {
            contentIdInput.value = slotNumber;
        }
    });
    syncPresetOrder(board);
}

function submitStationToSlot(station, slotNumber) {
    const form = document.getElementById("station-drop-form");
    if (!form) {
        return;
    }

    form.querySelector('[name="preset_number"]').value = slotNumber;
    form.querySelector('[name="station_id"]').value = station.stationId;
    form.querySelector('[name="station_name"]').value = station.stationName;
    form.querySelector('[name="image_url"]').value = station.stationImage || "";

    const board = document.querySelector("[data-preset-board]");
    const targetSlot = Array.from(board?.querySelectorAll(".preset-slot") || []).find(
        (slot) => slot.dataset.slot === String(slotNumber)
    );
    targetSlot?.classList.add("is-saving-preset");

    fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: {
            "X-SoundCork-Request": "fetch",
        },
    })
        .then(async (response) => {
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || "Preset save failed");
            }
            if (Array.isArray(payload.preset_slots) && payload.preset_slots.length) {
                renderPresetSlots(payload.preset_slots);
            } else if (targetSlot && payload.preset) {
                renderPresetSlot(targetSlot, {
                    number: Number(slotNumber),
                    preset: payload.preset,
                });
            }
            if (board) {
                syncPresetOrder(board);
            }
            showTransientMessage(
                payload.notice ||
                    tr("saved_to_preset", "Saved {name} to preset {preset_number}", {
                        name: station.stationName,
                        station_name: station.stationName,
                        preset_number: slotNumber,
                    }),
                "notice"
            );
            document.dispatchEvent(new CustomEvent("soundcork:presets-updated"));
        })
        .catch(() => {
            HTMLFormElement.prototype.submit.call(form);
        })
        .finally(() => {
            targetSlot?.classList.remove("is-saving-preset");
        });
}

const DRAG_START_DISTANCE = 14;
const DEFAULT_PRESET_THUMBNAIL_SIZE_PX = 104;
const MAX_PRESET_THUMBNAIL_SIZE_PX = 220;
const DEFAULT_PRESET_LONG_PRESS_DELAY_MS = 2000;
const PRESET_LONG_PRESS_CANCEL_DISTANCE = 54;
const DEFAULT_TOPBAR_LONG_PRESS_DELAY_MS = 900;
const TOPBAR_LONG_PRESS_CANCEL_DISTANCE = 26;
const PRESET_SEARCH_DEBOUNCE_MS = 3000;
const DEFAULT_PRESET_SEARCH_RESULT_DELAY_MS = 55;
const DEFAULT_TIMER_JOB_VISIBLE_COUNT = 5;
const DEFAULT_NOW_PLAYING_POLL_INTERVAL_MS = 15000;
const DEFAULT_VOLUME_POLL_INTERVAL_MS = 15000;
const MESSAGE_DISMISS_DELAY_MS = 7000;
const LIVE_TITLE_OVERFLOW_TOLERANCE = 3;
const PLAYBACK_OPTIMISTIC_WINDOW_MS = 8000;
const DEFAULT_TOPBAR_ORDER = ["status", "settings", "logout", "dashboard", "home"];
const DEFAULT_TOPBAR_ROW = {
    status: "primary",
    settings: "primary",
    logout: "primary",
    dashboard: "secondary",
    home: "secondary",
};
let optimisticPlaybackName = "";
let optimisticPlaybackUntil = 0;

function isPrimaryPointer(event) {
    return event.isPrimary !== false && (event.button === undefined || event.button === 0);
}

function dragDistance(startX, startY, currentX, currentY) {
    return Math.hypot(currentX - startX, currentY - startY);
}

function createDragGhost(source, className, rect) {
    const ghost = source.cloneNode(true);
    ghost.classList.add(className);
    ghost.setAttribute("aria-hidden", "true");
    ghost.removeAttribute("id");
    ghost.querySelectorAll("[id]").forEach((element) => {
        element.removeAttribute("id");
    });
    ghost.querySelectorAll("button, input, select, textarea, a").forEach((element) => {
        element.setAttribute("tabindex", "-1");
    });
    Object.assign(ghost.style, {
        left: `${rect.left}px`,
        top: `${rect.top}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
    });
    document.body.appendChild(ghost);
    return ghost;
}

function moveDragGhost(ghost, event, offsetX, offsetY) {
    ghost.style.left = `${event.clientX - offsetX}px`;
    ghost.style.top = `${event.clientY - offsetY}px`;
}

function autoScrollForPointer(clientY) {
    const edgeSize = 74;
    const scrollStep = 18;
    if (clientY < edgeSize) {
        window.scrollBy({ top: -scrollStep, behavior: "auto" });
    } else if (clientY > window.innerHeight - edgeSize) {
        window.scrollBy({ top: scrollStep, behavior: "auto" });
    }
}

function markSlotDropTarget(slot) {
    document.querySelectorAll(".preset-slot.is-station-drop-target").forEach((item) => {
        if (item !== slot) {
            item.classList.remove("is-station-drop-target");
        }
    });
    if (slot) {
        slot.classList.add("is-station-drop-target");
    }
}

function clearSlotDropTargets() {
    document.querySelectorAll(".preset-slot.is-station-drop-target").forEach((slot) => {
        slot.classList.remove("is-station-drop-target");
    });
}

function readMiniappSettings() {
    const element = document.getElementById("miniapp-settings");
    if (!element) {
        return {};
    }
    try {
        const settings = JSON.parse(element.textContent || "{}");
        return settings && typeof settings === "object" ? settings : {};
    } catch {
        return {};
    }
}

const miniappSettings = readMiniappSettings();
function tr(key, fallback = "", values = {}) {
    const translations = miniappSettings.translations || {};
    let text = translations[key] || fallback || key;
    Object.entries(values || {}).forEach(([name, value]) => {
        text = text.replaceAll(`{${name}}`, String(value));
    });
    return text;
}

const DEFAULT_PANEL_STYLE = {
    background_color: "#fbf7ef",
    text_color: "#1d2329",
    border_color: "#ded3c3",
    button_background_color: "#f6efe4",
};
const PANEL_DISPLAY_NAME_KEYS = {
    volume: ["volume", "Volume"],
    sources: ["sources", "Sources"],
    presets: ["presets", "Presets"],
    timer: ["sleep_alarm", "Sleep & Alarm"],
    "custom-stream": ["direct_stream", "Direct Stream"],
    backup: ["backup", "Backup"],
    radios: ["radios", "Radios"],
    "settings-language": ["language", "Language"],
    "settings-account": ["account", "Account"],
    "settings-provider": ["radio_search", "Radio Search"],
    "settings-network": ["radio_network", "Radio Network"],
    "settings-usability": ["usability", "Usability"],
    "settings-visual": ["visual", "Visual"],
};
const TIMER_SECTION_LABEL_KEYS = {
    sleep: ["sleep_section", "Sleep"],
    alarm: ["alarm_section", "Alarm"],
    jobs: ["scheduled_radio_actions", "Scheduled radio actions"],
};

function normalizePanelOrder(order) {
    if (!Array.isArray(order)) {
        return [];
    }
    return Array.from(new Set(order.filter(Boolean)));
}

function normalizeTopbarOrder(order) {
    if (!Array.isArray(order)) {
        return DEFAULT_TOPBAR_ORDER.slice();
    }
    const normalized = order.filter(
        (item, index) =>
            DEFAULT_TOPBAR_ORDER.includes(item) && order.indexOf(item) === index
    );
    DEFAULT_TOPBAR_ORDER.forEach((item) => {
        if (!normalized.includes(item)) {
            normalized.push(item);
        }
    });
    return normalized;
}

function normalizeTopbarRow(rows) {
    const normalized = { ...DEFAULT_TOPBAR_ROW };
    if (!rows || typeof rows !== "object" || Array.isArray(rows)) {
        return normalized;
    }
    Object.entries(rows).forEach(([itemId, rowId]) => {
        if (
            DEFAULT_TOPBAR_ORDER.includes(itemId) &&
            (rowId === "primary" || rowId === "secondary")
        ) {
            normalized[itemId] = rowId;
        }
    });
    return normalized;
}

function normalizeHexColor(value, fallback = "#000000") {
    const color = String(value || "").trim();
    return /^#[0-9a-f]{6}$/i.test(color) ? color.toLowerCase() : fallback;
}

function normalizePanelStyle(style = {}) {
    const backgroundColor = normalizeHexColor(
        style.background_color,
        DEFAULT_PANEL_STYLE.background_color
    );
    const textColor = normalizeHexColor(style.text_color, DEFAULT_PANEL_STYLE.text_color);
    return {
        background_color: backgroundColor,
        text_color: textColor,
        border_color: normalizeHexColor(
            style.border_color,
            DEFAULT_PANEL_STYLE.border_color
        ),
        button_background_color: normalizeHexColor(
            style.button_background_color,
            DEFAULT_PANEL_STYLE.button_background_color
        ),
    };
}

function panelStylesEqual(first, second) {
    return (
        first.background_color === second.background_color &&
        first.text_color === second.text_color &&
        first.border_color === second.border_color &&
        first.button_background_color === second.button_background_color
    );
}

function readGlobalPanelStyle() {
    return normalizePanelStyle(miniappSettings.global_panel_style || {});
}

function readPanelOrder(surface) {
    return normalizePanelOrder(miniappSettings.panel_order?.[surface || "default"]);
}

function readPanelStyle(panelId) {
    return normalizePanelStyle(
        miniappSettings.panel_style?.[panelId] || readGlobalPanelStyle()
    );
}

function writePanelStyle(panelId, style, persist = true) {
    if (!panelId) {
        return;
    }
    miniappSettings.panel_style = miniappSettings.panel_style || {};
    miniappSettings.panel_style[panelId] = normalizePanelStyle(style);
    applyPanelStyleById(panelId);
    if (persist) {
        persistPreferenceSettings({ panel_style: miniappSettings.panel_style }).then(
            (payload) => {
                if (payload?.panel_style) {
                    miniappSettings.panel_style = payload.panel_style;
                    applyPanelStyleById(panelId);
                }
            }
        );
    }
}

function resetPanelStyle(panelId) {
    if (!panelId) {
        return Promise.resolve({});
    }
    miniappSettings.panel_style = miniappSettings.panel_style || {};
    delete miniappSettings.panel_style[panelId];
    applyPanelStyleById(panelId);
    return persistPreferenceSettings({ panel_style: miniappSettings.panel_style }).then(
        (payload) => {
            if (payload?.panel_style) {
                miniappSettings.panel_style = payload.panel_style;
                applyPanelStyleById(panelId);
            }
            return payload;
        }
    );
}

function applyPanelStyle(panel) {
    const panelId = panel?.dataset?.panelId;
    if (!panelId) {
        return;
    }

    const storedStyle = miniappSettings.panel_style?.[panelId];
    const globalStyle = readGlobalPanelStyle();
    const hasGlobalStyle = !panelStylesEqual(globalStyle, DEFAULT_PANEL_STYLE);
    if (!storedStyle && !hasGlobalStyle) {
        panel.classList.remove("has-custom-panel-style");
        panel.style.removeProperty("--panel-bg");
        panel.style.removeProperty("--panel-text");
        panel.style.removeProperty("--panel-border");
        panel.style.removeProperty("--panel-button-bg");
        return;
    }

    const style = normalizePanelStyle(storedStyle || globalStyle);
    panel.classList.add("has-custom-panel-style");
    panel.style.setProperty("--panel-bg", style.background_color);
    panel.style.setProperty("--panel-text", style.text_color);
    panel.style.setProperty("--panel-border", style.border_color);
    panel.style.setProperty("--panel-button-bg", style.button_background_color);
}

function applyPanelStyleById(panelId) {
    document
        .querySelectorAll(".reorder-panel[data-panel-id]")
        .forEach((panel) => {
            if (panel.dataset.panelId === panelId) {
                applyPanelStyle(panel);
            }
        });
}

function applyAllPanelStyles() {
    document.querySelectorAll(".reorder-panel[data-panel-id]").forEach(applyPanelStyle);
}

function persistLayoutSettings() {
    fetch("/miniapp/settings/layout", {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-SoundCork-Request": "fetch",
        },
        body: JSON.stringify({
            panel_order: miniappSettings.panel_order || {},
            panel_surface: miniappSettings.panel_surface || {},
            topbar_order: normalizeTopbarOrder(miniappSettings.topbar_order),
            topbar_row: normalizeTopbarRow(miniappSettings.topbar_row),
        }),
    }).catch(() => {
        // The layout remains usable for this page load; the next successful drag will retry.
    });
}

function writePanelOrder(surface, order, persist = true) {
    miniappSettings.panel_order = miniappSettings.panel_order || {};
    miniappSettings.panel_order[surface || "default"] = normalizePanelOrder(order);
    if (persist) {
        persistLayoutSettings();
    }
}

function writePanelSurface(panelId, surface, persist = true) {
    miniappSettings.panel_surface = miniappSettings.panel_surface || {};
    miniappSettings.panel_surface[panelId] = surface;
    if (persist) {
        persistLayoutSettings();
    }
}

function readTopbarOrder() {
    return normalizeTopbarOrder(miniappSettings.topbar_order);
}

function readTopbarRow() {
    return normalizeTopbarRow(miniappSettings.topbar_row);
}

function writeTopbarOrder(order, persist = true) {
    miniappSettings.topbar_order = normalizeTopbarOrder(order);
    if (persist) {
        persistLayoutSettings();
    }
}

function writeTopbarRow(rows, persist = true) {
    miniappSettings.topbar_row = normalizeTopbarRow(rows);
    if (persist) {
        persistLayoutSettings();
    }
}

function presetDragOpacity() {
    const opacity = Number(miniappSettings.preset_drag_opacity);
    if (!Number.isFinite(opacity)) {
        return 0.58;
    }
    return Math.min(1, Math.max(0.15, opacity));
}

function presetThumbnailSizePx() {
    const size = Number(miniappSettings.preset_thumbnail_size_px);
    if (!Number.isFinite(size)) {
        return DEFAULT_PRESET_THUMBNAIL_SIZE_PX;
    }
    return Math.min(MAX_PRESET_THUMBNAIL_SIZE_PX, Math.max(80, Math.round(size)));
}

function applyPresetThumbnailSize() {
    const size = presetThumbnailSizePx();
    document.querySelectorAll("[data-preset-board]").forEach((board) => {
        board.style.setProperty("--preset-tile-size", `${size}px`);
    });
}

function presetLongPressDelayMs() {
    const delay = Number(miniappSettings.preset_long_press_delay_ms);
    if (!Number.isFinite(delay)) {
        return DEFAULT_PRESET_LONG_PRESS_DELAY_MS;
    }
    return Math.min(4000, Math.max(500, Math.round(delay)));
}

function presetSearchResultDelayMs() {
    const delay = Number(miniappSettings.preset_search_result_delay_ms);
    if (!Number.isFinite(delay)) {
        return DEFAULT_PRESET_SEARCH_RESULT_DELAY_MS;
    }
    return Math.min(250, Math.max(0, Math.round(delay)));
}

function timerJobVisibleCount() {
    const count = Number(miniappSettings.timer_job_visible_count);
    if (!Number.isFinite(count)) {
        return DEFAULT_TIMER_JOB_VISIBLE_COUNT;
    }
    return Math.min(12, Math.max(1, Math.round(count)));
}

function applyTimerJobVisibleCount() {
    const count = timerJobVisibleCount();
    const rowHeightRem = 3.7;
    const gapRem = 0.55;
    const maxHeightRem = count * rowHeightRem + Math.max(0, count - 1) * gapRem;
    document.querySelectorAll(".timer-job-scroll").forEach((container) => {
        container.style.setProperty(
            "--timer-job-scroll-max-height",
            `${maxHeightRem.toFixed(2)}rem`
        );
    });
}

function topbarLongPressDelayMs() {
    const delay = Number(miniappSettings.topbar_long_press_delay_ms);
    if (!Number.isFinite(delay)) {
        return DEFAULT_TOPBAR_LONG_PRESS_DELAY_MS;
    }
    return Math.min(4000, Math.max(500, Math.round(delay)));
}

function nowPlayingPollIntervalMs() {
    const interval = Number(miniappSettings.now_playing_poll_interval_ms);
    if (!Number.isFinite(interval)) {
        return DEFAULT_NOW_PLAYING_POLL_INTERVAL_MS;
    }
    return Math.min(60000, Math.max(3000, Math.round(interval)));
}

function volumePollIntervalMs() {
    const interval = Number(miniappSettings.volume_poll_interval_ms);
    if (!Number.isFinite(interval)) {
        return DEFAULT_VOLUME_POLL_INTERVAL_MS;
    }
    return Math.min(60000, Math.max(3000, Math.round(interval)));
}

function readPanelLabels() {
    return miniappSettings.panel_label && typeof miniappSettings.panel_label === "object"
        ? miniappSettings.panel_label
        : {};
}

function defaultPanelDisplayName(panel) {
    const panelId = panel.dataset.panelId || "";
    const displayKey = PANEL_DISPLAY_NAME_KEYS[panelId];
    if (!panel.dataset.panelDefaultName) {
        panel.dataset.panelDefaultName =
            panel.dataset.panelName?.trim() ||
            (displayKey ? tr(displayKey[0], displayKey[1]) : "") ||
            panel
                .querySelector(":scope > .section-heading h2, :scope > .control-title, h1")
                ?.textContent?.trim() ||
            tr("panel", "Panel");
    }
    return panel.dataset.panelDefaultName;
}

function panelLabelValue(panelId, fallback) {
    const labels = readPanelLabels();
    return Object.prototype.hasOwnProperty.call(labels, panelId)
        ? String(labels[panelId] ?? "")
        : fallback;
}

function applyPanelLabel(panel) {
    const panelId = panel.dataset.panelId || "";
    if (!panelId) {
        return;
    }

    const defaultLabel = defaultPanelDisplayName(panel);
    const label = panelLabelValue(panelId, defaultLabel);
    const empty = label.trim() === "";
    const title = panel.querySelector(
        ":scope > .section-heading h2, :scope > .control-title"
    );
    if (title) {
        title.textContent = label;
        title.hidden = empty;
    }

    const heading = panel.querySelector(":scope > .section-heading");
    if (heading) {
        const hasActions = Boolean(heading.querySelector(".section-actions > *"));
        heading.hidden = empty && !hasActions;
    }

    panel.dataset.panelName = label;
    panel.classList.toggle("has-empty-panel-label", empty);
    const handle = panel.querySelector(":scope > [data-panel-handle]");
    if (handle) {
        handle.setAttribute(
            "aria-label",
            tr("move_panel", "Move {panel} panel", {
                panel: empty ? defaultLabel : label,
            })
        );
    }
}

function applyPanelLabelById(panelId) {
    document.querySelectorAll(".reorder-panel[data-panel-id]").forEach((panel) => {
        if (panel.dataset.panelId === panelId) {
            applyPanelLabel(panel);
        }
    });
}

function applyAllPanelLabels() {
    document.querySelectorAll(".reorder-panel[data-panel-id]").forEach(applyPanelLabel);
}

function writePanelLabel(panelId, label, persist = true) {
    if (!panelId) {
        return;
    }
    miniappSettings.panel_label = readPanelLabels();
    miniappSettings.panel_label[panelId] = String(label ?? "").trim().slice(0, 80);
    applyPanelLabelById(panelId);
    if (persist) {
        persistPreferenceSettings({ panel_label: miniappSettings.panel_label }).then(
            (payload) => {
                if (payload?.panel_label) {
                    miniappSettings.panel_label = payload.panel_label;
                    applyPanelLabelById(panelId);
                }
            }
        );
    }
}

function readTimerSectionLabels() {
    return miniappSettings.timer_section_label &&
        typeof miniappSettings.timer_section_label === "object"
        ? miniappSettings.timer_section_label
        : {};
}

function defaultTimerSectionLabel(labelKey) {
    const translation = TIMER_SECTION_LABEL_KEYS[labelKey];
    if (!translation) {
        return "";
    }
    return tr(translation[0], translation[1]);
}

function timerSectionLabelValue(labelKey) {
    const labels = readTimerSectionLabels();
    return Object.prototype.hasOwnProperty.call(labels, labelKey)
        ? String(labels[labelKey] ?? "")
        : defaultTimerSectionLabel(labelKey);
}

function applyTimerSectionLabels() {
    document.querySelectorAll("[data-timer-section-label]").forEach((element) => {
        const labelKey = element.dataset.timerSectionLabel || "";
        const label = timerSectionLabelValue(labelKey);
        element.textContent = label;
        element.hidden = label.trim() === "";
    });
}

function writeTimerSectionLabel(labelKey, label, persist = true) {
    if (!Object.prototype.hasOwnProperty.call(TIMER_SECTION_LABEL_KEYS, labelKey)) {
        return;
    }
    miniappSettings.timer_section_label = readTimerSectionLabels();
    miniappSettings.timer_section_label[labelKey] = String(label ?? "")
        .trim()
        .slice(0, 80);
    applyTimerSectionLabels();
    if (persist) {
        persistPreferenceSettings({
            timer_section_label: miniappSettings.timer_section_label,
        }).then((payload) => {
            if (payload?.timer_section_label) {
                miniappSettings.timer_section_label = payload.timer_section_label;
                applyTimerSectionLabels();
            }
        });
    }
}

function persistPreferenceSettings(payload) {
    Object.assign(miniappSettings, payload);
    return fetch("/miniapp/settings/preferences", {
        method: "POST",
        credentials: "same-origin",
        headers: {
            "Content-Type": "application/json",
            "X-SoundCork-Request": "fetch",
        },
        body: JSON.stringify(payload),
    })
        .then(async (response) => {
            const data = await response.json().catch(() => ({}));
            if (response.ok && data?.ok) {
                Object.assign(miniappSettings, data);
            }
            return data;
        })
        .catch(() => {
            // The current page keeps the value; another save attempt can persist it later.
            return {};
        });
}

function visibleReorderPanels(stack) {
    return Array.from(stack.querySelectorAll(":scope > .reorder-panel")).filter(
        (panel) => !panel.hidden
    );
}

function panelSurface(panel, fallbackSurface) {
    const panelId = panel.dataset.panelId;
    if (!panelId) {
        return fallbackSurface;
    }
    return (
        miniappSettings.panel_surface?.[panelId] ||
        panel.dataset.panelDefaultSurface ||
        fallbackSurface
    );
}

function applyPanelVisibility(stack) {
    const surface = stack.dataset.panelStack || "default";
    stack.querySelectorAll(":scope > .reorder-panel").forEach((panel) => {
        panel.hidden = panelSurface(panel, surface) !== surface;
    });
    stack.classList.toggle(
        "has-no-visible-panels",
        visibleReorderPanels(stack).length === 0
    );
}

function movePanelToSurface(panelId, targetSurface) {
    if (!panelId || !targetSurface) {
        return;
    }
    writePanelSurface(panelId, targetSurface, false);

    document.querySelectorAll("[data-panel-stack]").forEach((stack) => {
        const surface = stack.dataset.panelStack || "default";
        const order = readPanelOrder(surface).filter((item) => item !== panelId);
        if (surface === targetSurface) {
            order.unshift(panelId);
        }
        writePanelOrder(surface, order, false);
    });
    const targetOrder = readPanelOrder(targetSurface).filter((item) => item !== panelId);
    targetOrder.unshift(panelId);
    writePanelOrder(targetSurface, targetOrder, false);
    persistLayoutSettings();
}

function savePanelOrder(stack) {
    const surface = stack.dataset.panelStack || "default";
    const order = visibleReorderPanels(stack)
        .map((panel) => panel.dataset.panelId)
        .filter(Boolean);
    writePanelOrder(surface, order);
}

function applySavedPanelOrder(stack) {
    const order = readPanelOrder(stack.dataset.panelStack || "default");
    if (!Array.isArray(order) || order.length === 0) {
        return;
    }

    const panelsById = new Map(
        Array.from(stack.querySelectorAll(":scope > .reorder-panel")).map((panel) => [
            panel.dataset.panelId,
            panel,
        ])
    );
    const orderedPanels = [];
    order.forEach((panelId) => {
        const panel = panelsById.get(panelId);
        if (panel) {
            orderedPanels.push(panel);
            panelsById.delete(panelId);
        }
    });
    Array.from(panelsById.values()).forEach((panel) => orderedPanels.push(panel));
    orderedPanels.forEach((panel) => stack.appendChild(panel));
}

function targetAllowsPanel(target, panel) {
    if (!target || !panel) {
        return false;
    }
    const targetSurface = target.dataset.panelDropTarget;
    const sourceSurface = panel.closest("[data-panel-stack]")?.dataset.panelStack;
    if (!targetSurface || targetSurface === sourceSurface) {
        return false;
    }
    return Boolean(panel.dataset.panelId);
}

function panelDropTargetAtPoint(event, panel) {
    const element = document.elementFromPoint(event.clientX, event.clientY);
    const target = element?.closest("[data-panel-drop-target]");
    return targetAllowsPanel(target, panel) ? target : null;
}

function markPanelDropTarget(target) {
    document
        .querySelectorAll("[data-panel-drop-target].is-panel-drop-target")
        .forEach((item) => {
            if (item !== target) {
                item.classList.remove("is-panel-drop-target");
            }
        });
    if (target) {
        target.classList.add("is-panel-drop-target");
    }
}

function clearPanelDropTargets() {
    document
        .querySelectorAll("[data-panel-drop-target].is-panel-drop-target")
        .forEach((item) => {
            item.classList.remove("is-panel-drop-target");
        });
}

function topbarItems(nav) {
    return Array.from(nav.querySelectorAll("[data-topbar-item]"));
}

function topbarRows(nav) {
    return Array.from(nav.querySelectorAll("[data-topbar-row]"));
}

function topbarRowForItem(nav, itemId) {
    const rows = readTopbarRow();
    const rowId = rows[itemId] || DEFAULT_TOPBAR_ROW[itemId] || "primary";
    return (
        nav.querySelector(`[data-topbar-row="${rowId}"]`) ||
        nav.querySelector("[data-topbar-row]") ||
        nav
    );
}

function saveTopbarOrder(nav) {
    const rows = {};
    topbarItems(nav).forEach((item) => {
        const itemId = item.dataset.topbarItem;
        const rowId = item.closest("[data-topbar-row]")?.dataset.topbarRow;
        if (itemId && rowId) {
            rows[itemId] = rowId;
        }
    });
    writeTopbarRow(rows, false);
    writeTopbarOrder(
        topbarRows(nav).flatMap((row) =>
            topbarItems(row)
                .map((item) => item.dataset.topbarItem)
                .filter(Boolean)
        )
    );
}

function applySavedTopbarOrder(nav) {
    const itemsById = new Map(
        topbarItems(nav).map((item) => [item.dataset.topbarItem, item])
    );
    readTopbarOrder().forEach((itemId) => {
        const item = itemsById.get(itemId);
        if (item) {
            topbarRowForItem(nav, itemId).appendChild(item);
            itemsById.delete(itemId);
        }
    });
    itemsById.forEach((item) => {
        topbarRowForItem(nav, item.dataset.topbarItem).appendChild(item);
    });
}

function initTopbarReorder() {
    const nav = document.querySelector("[data-topnav]");
    if (!nav) {
        return;
    }

    applySavedTopbarOrder(nav);
    nav.querySelectorAll("a, img, button").forEach((element) => {
        element.setAttribute("draggable", "false");
    });

    let pending = null;
    let drag = null;
    let suppressClick = false;

    function rowAtPoint(pointerX, pointerY) {
        const directRow = document
            .elementFromPoint(pointerX, pointerY)
            ?.closest("[data-topbar-row]");
        if (directRow && nav.contains(directRow)) {
            return directRow;
        }

        return (
            topbarRows(nav)
                .map((row) => {
                    const rect = row.getBoundingClientRect();
                    const centerY = rect.top + rect.height / 2;
                    return { row, distance: Math.abs(pointerY - centerY) };
                })
                .sort((first, second) => first.distance - second.distance)[0]?.row ||
            drag?.currentRow ||
            null
        );
    }

    function referenceForPoint(row, pointerX) {
        const candidates = topbarItems(row)
            .filter((item) => item !== drag.item)
            .map((item) => ({
                item,
                rect: item.getBoundingClientRect(),
            }))
            .sort((first, second) => first.rect.left - second.rect.left);

        return (
            candidates.find(({ rect }) => pointerX < rect.left + rect.width / 2)
                ?.item || null
        );
    }

    function cleanupPending() {
        if (!pending) {
            return;
        }
        window.clearTimeout(pending.timer);
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerUp);
        window.removeEventListener("pointercancel", handlePointerCancel);
        try {
            pending.item.releasePointerCapture(pending.pointerId);
        } catch {
            // The browser may already have released the pointer.
        }
        pending = null;
    }

    function beginTopbarDrag(event) {
        if (!pending || drag) {
            return;
        }
        const item = pending.item;
        const rect = item.getBoundingClientRect();
        const pointerX = event?.clientX ?? pending.lastX ?? pending.startX;
        const pointerY = event?.clientY ?? pending.lastY ?? pending.startY;
        const placeholder = document.createElement("div");
        placeholder.className = "topbar-placeholder";
        placeholder.style.width = `${rect.width}px`;
        placeholder.style.height = `${rect.height}px`;
        placeholder.setAttribute("aria-hidden", "true");
        const sourceRow = item.closest("[data-topbar-row]") || nav;
        sourceRow.insertBefore(placeholder, item);

        const ghost = createDragGhost(item, "topbar-drag-ghost", rect);
        item.hidden = true;
        item.classList.add("is-topbar-dragging");
        document.body.classList.add("is-dragging-panel");
        suppressClick = true;
        try {
            item.setPointerCapture(pending.pointerId);
        } catch {
            // Some browsers only allow capture while the pointer is still active.
        }

        drag = {
            item,
            placeholder,
            ghost,
            pointerId: pending.pointerId,
            currentRow: sourceRow,
            offsetX: pointerX - rect.left,
            offsetY: pointerY - rect.top,
            initialLayout: topbarRows(nav)
                .map((row) =>
                    topbarItems(row)
                        .map((topbarItem) => topbarItem.dataset.topbarItem)
                        .join(",")
                )
                .join("|"),
        };
        moveDragGhost(ghost, { clientX: pointerX, clientY: pointerY }, drag.offsetX, drag.offsetY);
    }

    function finishTopbarDrag(commit) {
        if (!drag) {
            return;
        }

        if (commit) {
            drag.placeholder.parentElement.insertBefore(drag.item, drag.placeholder);
        }
        drag.item.hidden = false;
        drag.item.classList.remove("is-topbar-dragging");
        drag.placeholder.remove();
        drag.ghost.remove();
        document.body.classList.remove("is-dragging-panel");

        if (
            commit &&
            topbarRows(nav)
                .map((row) =>
                    topbarItems(row)
                        .map((item) => item.dataset.topbarItem)
                        .join(",")
                )
                .join("|") !== drag.initialLayout
        ) {
            saveTopbarOrder(nav);
        }
        drag = null;
    }

    function handlePointerMove(event) {
        if (!pending || event.pointerId !== pending.pointerId) {
            return;
        }
        pending.lastX = event.clientX;
        pending.lastY = event.clientY;
        if (!drag) {
            if (
                event.pointerType === "touch" &&
                dragDistance(
                    pending.startX,
                    pending.startY,
                    event.clientX,
                    event.clientY
                ) >= TOPBAR_LONG_PRESS_CANCEL_DISTANCE
            ) {
                cleanupPending();
            }
            return;
        }
        if (event.pointerId !== drag.pointerId) {
            return;
        }

        event.preventDefault();
        moveDragGhost(drag.ghost, event, drag.offsetX, drag.offsetY);
        const targetRow = rowAtPoint(event.clientX, event.clientY) || drag.currentRow;
        drag.currentRow = targetRow;
        targetRow.insertBefore(
            drag.placeholder,
            referenceForPoint(targetRow, event.clientX)
        );
        autoScrollForPointer(event.clientY);
    }

    function handlePointerUp(event) {
        if (!pending || event.pointerId !== pending.pointerId) {
            return;
        }
        const hadDrag = Boolean(drag);
        if (drag) {
            event.preventDefault();
            finishTopbarDrag(true);
        }
        cleanupPending();
        if (hadDrag) {
            window.setTimeout(() => {
                suppressClick = false;
            }, 250);
        } else {
            suppressClick = false;
        }
    }

    function handlePointerCancel(event) {
        if (!pending || event.pointerId !== pending.pointerId) {
            return;
        }
        finishTopbarDrag(false);
        cleanupPending();
        suppressClick = false;
    }

    nav.addEventListener(
        "click",
        (event) => {
            if (!suppressClick) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            suppressClick = false;
        },
        true
    );
    nav.addEventListener("dragstart", (event) => {
        if (event.target.closest("[data-topbar-item]")) {
            event.preventDefault();
        }
    });
    nav.addEventListener("contextmenu", (event) => {
        if (event.target.closest("[data-topbar-item]")) {
            event.preventDefault();
        }
    });

    topbarItems(nav).forEach((item) => {
        item.addEventListener("pointerdown", (event) => {
            if (
                !isPrimaryPointer(event) ||
                pending ||
                drag ||
                event.target.closest("input, select, textarea")
            ) {
                return;
            }
            pending = {
                item,
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                lastX: event.clientX,
                lastY: event.clientY,
                timer: window.setTimeout(() => {
                    beginTopbarDrag();
                }, topbarLongPressDelayMs()),
            };
            window.addEventListener("pointermove", handlePointerMove, {
                passive: false,
            });
            window.addEventListener("pointerup", handlePointerUp);
            window.addEventListener("pointercancel", handlePointerCancel);
        });
    });
}

function panelDisplayName(panel) {
    const panelId = panel.dataset.panelId || "";
    return panelLabelValue(panelId, defaultPanelDisplayName(panel));
}

function closeOtherPanelSettings(panel) {
    document.querySelectorAll(".panel-settings").forEach((settings) => {
        if (!panel || !panel.contains(settings)) {
            settings.remove();
        }
    });
    document.querySelectorAll("[data-panel-handle][aria-expanded='true']").forEach((handle) => {
        if (!panel || !panel.contains(handle)) {
            handle.setAttribute("aria-expanded", "false");
        }
    });
}

function createColorField(labelText, value) {
    const field = document.createElement("label");
    field.className = "field color-field";
    const label = document.createElement("span");
    label.textContent = labelText;
    const input = document.createElement("input");
    input.type = "color";
    input.value = normalizeHexColor(value, "#000000");
    field.append(label, input);
    return { field, input };
}

function appendPanelColorSettings(settings, panel) {
    const panelId = panel.dataset.panelId;
    if (!panelId) {
        return;
    }

    const currentStyle = readPanelStyle(panelId);
    const group = document.createElement("div");
    group.className = "panel-color-grid";
    const background = createColorField(
        tr("background", "Background"),
        currentStyle.background_color
    );
    const text = createColorField(tr("text", "Text"), currentStyle.text_color);
    const border = createColorField(tr("border", "Border"), currentStyle.border_color);
    const buttonBackground = createColorField(
        tr("button_background", "Button Background"),
        currentStyle.button_background_color
    );
    group.append(background.field, text.field, border.field, buttonBackground.field);
    settings.appendChild(group);

    const readInputs = () => ({
        background_color: background.input.value,
        text_color: text.input.value,
        border_color: border.input.value,
        button_background_color: buttonBackground.input.value,
    });
    const writeInputs = (style) => {
        const normalizedStyle = normalizePanelStyle(style);
        background.input.value = normalizedStyle.background_color;
        text.input.value = normalizedStyle.text_color;
        border.input.value = normalizedStyle.border_color;
        buttonBackground.input.value = normalizedStyle.button_background_color;
    };

    const applyInputs = (persist) => {
        writePanelStyle(panelId, readInputs(), persist);
    };

    [background.input, text.input, border.input, buttonBackground.input].forEach((input) => {
        input.addEventListener("input", () => applyInputs(false));
        input.addEventListener("change", () => applyInputs(true));
    });

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "compact-button";
    reset.textContent = tr("reset_panel_colors", "Reset Panel Colors");
    reset.addEventListener("click", () => {
        reset.disabled = true;
        writeInputs(readGlobalPanelStyle());
        resetPanelStyle(panelId)
            .then(() => {
                writeInputs(readPanelStyle(panelId));
            })
            .finally(() => {
                reset.disabled = false;
            });
    });
    settings.appendChild(reset);
}

function createPanelLabelField(panel) {
    const panelId = panel.dataset.panelId || "";
    const field = document.createElement("label");
    field.className = "field";
    const label = document.createElement("span");
    label.textContent = tr("panel_label", "Panel Label");
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 80;
    input.autocomplete = "off";
    input.value = panelDisplayName(panel);
    input.placeholder = defaultPanelDisplayName(panel);
    const hint = document.createElement("small");
    hint.className = "field-value";
    hint.textContent = tr(
        "empty_panel_label_hint",
        "Leave empty to hide this label."
    );

    let saveTimer = null;
    input.addEventListener("input", () => {
        writePanelLabel(panelId, input.value, false);
        window.clearTimeout(saveTimer);
        saveTimer = window.setTimeout(() => {
            writePanelLabel(panelId, input.value, true);
        }, 500);
    });
    input.addEventListener("change", () => {
        window.clearTimeout(saveTimer);
        writePanelLabel(panelId, input.value, true);
    });

    field.append(label, input, hint);
    return field;
}

function createTimerSectionLabelField(labelKey, fieldLabelKey, fallbackFieldLabel) {
    const field = document.createElement("label");
    field.className = "field";
    const label = document.createElement("span");
    label.textContent = tr(fieldLabelKey, fallbackFieldLabel);
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 80;
    input.autocomplete = "off";
    input.value = timerSectionLabelValue(labelKey);
    input.placeholder = defaultTimerSectionLabel(labelKey);
    const hint = document.createElement("small");
    hint.className = "field-value";
    hint.textContent = tr(
        "empty_section_label_hint",
        "Leave empty to hide this section label."
    );

    let saveTimer = null;
    input.addEventListener("input", () => {
        writeTimerSectionLabel(labelKey, input.value, false);
        window.clearTimeout(saveTimer);
        saveTimer = window.setTimeout(() => {
            writeTimerSectionLabel(labelKey, input.value, true);
        }, 500);
    });
    input.addEventListener("change", () => {
        window.clearTimeout(saveTimer);
        writeTimerSectionLabel(labelKey, input.value, true);
    });

    field.append(label, input, hint);
    return field;
}

function createPanelSettings(panel, handle) {
    const settings = document.createElement("div");
    settings.className = "panel-settings";
    settings.id = `panel-settings-${panel.dataset.panelId || "panel"}`;
    settings.setAttribute("role", "region");

    const header = document.createElement("div");
    header.className = "panel-settings-heading";
    const title = document.createElement("h3");
    title.textContent = tr("panel_settings", "Panel Settings");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "icon-button panel-settings-close";
    close.setAttribute(
        "aria-label",
        tr("close_panel_settings", "Close panel settings")
    );
    close.addEventListener("click", () => {
        settings.remove();
        handle.setAttribute("aria-expanded", "false");
    });
    header.append(title, close);
    settings.appendChild(header);
    settings.appendChild(createPanelLabelField(panel));

    appendPanelColorSettings(settings, panel);

    if (panel.dataset.panelId === "presets") {
        const thumbnailField = document.createElement("label");
        thumbnailField.className = "field";
        const thumbnailLabel = document.createElement("span");
        thumbnailLabel.textContent = tr("preset_thumbnail_size", "Preset Thumbnail Size");
        const thumbnailRange = document.createElement("input");
        thumbnailRange.type = "range";
        thumbnailRange.min = "80";
        thumbnailRange.max = String(MAX_PRESET_THUMBNAIL_SIZE_PX);
        thumbnailRange.step = "4";
        thumbnailRange.value = String(presetThumbnailSizePx());
        const thumbnailValue = document.createElement("small");
        thumbnailValue.className = "field-value";
        const updateThumbnailValue = () => {
            thumbnailValue.textContent = tr("pixels_value", "{value}px", {
                value: thumbnailRange.value,
            });
        };
        thumbnailRange.addEventListener("input", () => {
            updateThumbnailValue();
            miniappSettings.preset_thumbnail_size_px = Number(thumbnailRange.value);
            applyPresetThumbnailSize();
        });
        thumbnailRange.addEventListener("change", () => {
            persistPreferenceSettings({
                preset_thumbnail_size_px: Number(thumbnailRange.value),
            });
        });
        updateThumbnailValue();
        thumbnailField.append(thumbnailLabel, thumbnailRange, thumbnailValue);
        settings.appendChild(thumbnailField);

        const opacityField = document.createElement("label");
        opacityField.className = "field";
        const opacityLabel = document.createElement("span");
        opacityLabel.textContent = tr("dragged_tile_opacity", "Dragged Tile Opacity");
        const opacityRange = document.createElement("input");
        opacityRange.type = "range";
        opacityRange.min = "15";
        opacityRange.max = "100";
        opacityRange.step = "1";
        opacityRange.value = String(Math.round(presetDragOpacity() * 100));
        const opacityValue = document.createElement("small");
        opacityValue.className = "field-value";
        const updateOpacityValue = () => {
            opacityValue.textContent = `${opacityRange.value}%`;
        };
        opacityRange.addEventListener("input", () => {
            updateOpacityValue();
            miniappSettings.preset_drag_opacity = Number(opacityRange.value) / 100;
        });
        opacityRange.addEventListener("change", () => {
            persistPreferenceSettings({
                preset_drag_opacity: Number(opacityRange.value) / 100,
            });
        });
        updateOpacityValue();
        opacityField.append(opacityLabel, opacityRange, opacityValue);
        settings.appendChild(opacityField);

        const delayField = document.createElement("label");
        delayField.className = "field";
        const delayLabel = document.createElement("span");
        delayLabel.textContent = tr("hold_before_drag", "Hold Before Drag");
        const delayRange = document.createElement("input");
        delayRange.type = "range";
        delayRange.min = "500";
        delayRange.max = "4000";
        delayRange.step = "100";
        delayRange.value = String(presetLongPressDelayMs());
        const delayValue = document.createElement("small");
        delayValue.className = "field-value";
        const updateDelayValue = () => {
            delayValue.textContent = `${(Number(delayRange.value) / 1000).toFixed(1)}s`;
        };
        delayRange.addEventListener("input", () => {
            updateDelayValue();
            miniappSettings.preset_long_press_delay_ms = Number(delayRange.value);
        });
        delayRange.addEventListener("change", () => {
            persistPreferenceSettings({
                preset_long_press_delay_ms: Number(delayRange.value),
            });
        });
        updateDelayValue();
        delayField.append(delayLabel, delayRange, delayValue);
        settings.appendChild(delayField);

        const resultDelayField = document.createElement("label");
        resultDelayField.className = "field";
        const resultDelayLabel = document.createElement("span");
        resultDelayLabel.textContent = tr(
            "search_result_reveal_delay",
            "Search Result Reveal Delay"
        );
        const resultDelayRange = document.createElement("input");
        resultDelayRange.type = "range";
        resultDelayRange.min = "0";
        resultDelayRange.max = "250";
        resultDelayRange.step = "5";
        resultDelayRange.value = String(presetSearchResultDelayMs());
        const resultDelayValue = document.createElement("small");
        resultDelayValue.className = "field-value";
        const updateResultDelayValue = () => {
            resultDelayValue.textContent = tr(
                "milliseconds_per_result",
                "{value} ms per result",
                { value: resultDelayRange.value }
            );
        };
        resultDelayRange.addEventListener("input", () => {
            updateResultDelayValue();
            miniappSettings.preset_search_result_delay_ms = Number(
                resultDelayRange.value
            );
        });
        resultDelayRange.addEventListener("change", () => {
            persistPreferenceSettings({
                preset_search_result_delay_ms: Number(resultDelayRange.value),
            });
        });
        updateResultDelayValue();
        resultDelayField.append(
            resultDelayLabel,
            resultDelayRange,
            resultDelayValue
        );
        settings.appendChild(resultDelayField);
    }

    if (panel.dataset.panelId === "timer") {
        settings.append(
            createTimerSectionLabelField(
                "sleep",
                "sleep_section_label",
                "Sleep Label"
            ),
            createTimerSectionLabelField(
                "alarm",
                "alarm_section_label",
                "Alarm Label"
            ),
            createTimerSectionLabelField(
                "jobs",
                "planned_actions_section_label",
                "Planned Actions Label"
            )
        );

        const visibleRowsField = document.createElement("label");
        visibleRowsField.className = "field";
        const visibleRowsLabel = document.createElement("span");
        visibleRowsLabel.textContent = tr(
            "scheduled_actions_visible_rows",
            "Visible Scheduled Rows"
        );
        const visibleRowsRange = document.createElement("input");
        visibleRowsRange.type = "range";
        visibleRowsRange.min = "1";
        visibleRowsRange.max = "12";
        visibleRowsRange.step = "1";
        visibleRowsRange.value = String(timerJobVisibleCount());
        const visibleRowsValue = document.createElement("small");
        visibleRowsValue.className = "field-value";
        const updateVisibleRowsValue = () => {
            visibleRowsValue.textContent = tr("rows_value", "{value} rows", {
                value: visibleRowsRange.value,
            });
        };
        visibleRowsRange.addEventListener("input", () => {
            updateVisibleRowsValue();
            miniappSettings.timer_job_visible_count = Number(visibleRowsRange.value);
            applyTimerJobVisibleCount();
        });
        visibleRowsRange.addEventListener("change", () => {
            persistPreferenceSettings({
                timer_job_visible_count: Number(visibleRowsRange.value),
            }).then(() => {
                applyTimerJobVisibleCount();
            });
        });
        updateVisibleRowsValue();
        visibleRowsField.append(
            visibleRowsLabel,
            visibleRowsRange,
            visibleRowsValue
        );
        settings.appendChild(visibleRowsField);
    }

    return settings;
}

function togglePanelSettings(panel, handle) {
    const existing = panel.querySelector(":scope > .panel-settings");
    if (existing) {
        existing.remove();
        handle.setAttribute("aria-expanded", "false");
        return;
    }

    closeOtherPanelSettings(panel);
    const settings = createPanelSettings(panel, handle);
    panel.insertBefore(settings, handle.nextSibling);
    handle.setAttribute("aria-expanded", "true");
}

function applyPanelOrderFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const rawPanelOrder = params.get("panel_order");
    if (!rawPanelOrder) {
        return;
    }

    try {
        const panelOrder = JSON.parse(rawPanelOrder);
        if (Array.isArray(panelOrder)) {
            writePanelOrder("dashboard", panelOrder);
        }
    } catch {
        // Ignore malformed query values; the page can keep its current order.
    }

    params.delete("panel_order");
    const query = params.toString();
    window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
    );
}

function initPanelReorder() {
    document.querySelectorAll("[data-panel-stack]").forEach((stack) => {
        applySavedPanelOrder(stack);
        applyPanelVisibility(stack);

        stack.querySelectorAll("[data-panel-handle]").forEach((handle) => {
            let pending = null;
            let drag = null;

            function directPanels() {
                return visibleReorderPanels(stack);
            }

            function panelReferenceForY(pointerY) {
                return (
                    directPanels()
                        .filter((panel) => panel !== drag.panel)
                        .find((panel) => {
                            const rect = panel.getBoundingClientRect();
                            return pointerY < rect.top + rect.height / 2;
                        }) || null
                );
            }

            function finishDrag(commit) {
                if (!drag) {
                    return;
                }

                const dropTarget = commit ? drag.dropTarget : null;
                const droppedSurface = dropTarget?.dataset.panelDropTarget;
                const movedToOtherSurface = Boolean(
                    droppedSurface && droppedSurface !== stack.dataset.panelStack
                );

                if (commit && !movedToOtherSurface) {
                    stack.insertBefore(drag.panel, drag.placeholder);
                }
                drag.panel.hidden = false;
                drag.panel.classList.remove("is-panel-dragging");
                drag.placeholder.remove();
                drag.ghost.remove();
                stack.classList.remove("is-panel-reordering");
                document.body.classList.remove("is-dragging-panel");
                clearPanelDropTargets();

                if (movedToOtherSurface) {
                    movePanelToSurface(drag.panel.dataset.panelId, droppedSurface);
                    applyPanelVisibility(stack);
                } else if (commit) {
                    savePanelOrder(stack);
                }

                const redirectTarget = movedToOtherSurface ? dropTarget.href : "";
                drag = null;

                if (redirectTarget) {
                    window.location.href = redirectTarget;
                }
            }

            function cleanupPending() {
                if (!pending) {
                    return;
                }
                window.removeEventListener("pointermove", handlePointerMove);
                window.removeEventListener("pointerup", handlePointerUp);
                window.removeEventListener("pointercancel", handlePointerCancel);
                try {
                    handle.releasePointerCapture(pending.pointerId);
                } catch {
                    // The pointer may already be released by the browser.
                }
                pending = null;
            }

            function beginPanelDrag(event) {
                const panel = pending.panel;
                closeOtherPanelSettings();
                panel.querySelector(":scope > .panel-settings")?.remove();
                handle.setAttribute("aria-expanded", "false");

                const rect = panel.getBoundingClientRect();
                const placeholder = document.createElement("div");
                placeholder.className = "panel-placeholder";
                placeholder.style.height = `${rect.height}px`;
                placeholder.setAttribute("aria-hidden", "true");
                stack.insertBefore(placeholder, panel);

                const ghost = createDragGhost(panel, "panel-drag-ghost", rect);
                panel.hidden = true;
                panel.classList.add("is-panel-dragging");
                stack.classList.add("is-panel-reordering");
                document.body.classList.add("is-dragging-panel");

                drag = {
                    panel,
                    placeholder,
                    ghost,
                    pointerId: pending.pointerId,
                    offsetX: pending.startX - rect.left,
                    offsetY: pending.startY - rect.top,
                    dropTarget: null,
                };
                moveDragGhost(ghost, event, drag.offsetX, drag.offsetY);
            }

            function handlePointerMove(event) {
                if (!pending || event.pointerId !== pending.pointerId) {
                    return;
                }

                if (
                    !drag &&
                    dragDistance(
                        pending.startX,
                        pending.startY,
                        event.clientX,
                        event.clientY
                    ) >= DRAG_START_DISTANCE
                ) {
                    beginPanelDrag(event);
                }

                if (!drag || event.pointerId !== drag.pointerId) {
                    return;
                }

                event.preventDefault();
                moveDragGhost(drag.ghost, event, drag.offsetX, drag.offsetY);
                drag.dropTarget = panelDropTargetAtPoint(event, drag.panel);
                markPanelDropTarget(drag.dropTarget);
                if (!drag.dropTarget) {
                    stack.insertBefore(
                        drag.placeholder,
                        panelReferenceForY(event.clientY)
                    );
                }
                autoScrollForPointer(event.clientY);
            }

            function handlePointerUp(event) {
                if (!pending || event.pointerId !== pending.pointerId) {
                    return;
                }
                if (drag) {
                    event.preventDefault();
                    finishDrag(true);
                } else {
                    event.preventDefault();
                    togglePanelSettings(pending.panel, handle);
                }
                cleanupPending();
            }

            function handlePointerCancel(event) {
                if (!pending || event.pointerId !== pending.pointerId) {
                    return;
                }
                if (drag) {
                    finishDrag(false);
                }
                cleanupPending();
            }

            handle.addEventListener("click", (event) => {
                event.preventDefault();
                if (event.detail === 0) {
                    const panel = handle.closest(".reorder-panel");
                    if (panel && panel.parentElement === stack) {
                        togglePanelSettings(panel, handle);
                    }
                }
            });

            handle.addEventListener("pointerdown", (event) => {
                if (!isPrimaryPointer(event) || pending || drag) {
                    return;
                }
                const panel = handle.closest(".reorder-panel");
                if (!panel || panel.parentElement !== stack) {
                    return;
                }

                pending = {
                    panel,
                    pointerId: event.pointerId,
                    startX: event.clientX,
                    startY: event.clientY,
                };

                handle.setPointerCapture(event.pointerId);
                window.addEventListener("pointermove", handlePointerMove, {
                    passive: false,
                });
                window.addEventListener("pointerup", handlePointerUp);
                window.addEventListener("pointercancel", handlePointerCancel);
                event.preventDefault();
            });
        });
    });
}

function initVolumeForms() {
    document.querySelectorAll("[data-volume-form]").forEach((form) => {
        const range = form.querySelector("[data-volume-range]");
        if (!range) {
            return;
        }
        let editingTimer = null;
        const markEditing = () => {
            form.dataset.volumeUserEditing = "true";
            window.clearTimeout(editingTimer);
            editingTimer = window.setTimeout(() => {
                delete form.dataset.volumeUserEditing;
            }, 1500);
        };
        const releaseEditing = () => {
            window.clearTimeout(editingTimer);
            editingTimer = window.setTimeout(() => {
                delete form.dataset.volumeUserEditing;
            }, 600);
        };

        range.addEventListener("pointerdown", markEditing);
        range.addEventListener("input", markEditing);
        range.addEventListener("pointerup", releaseEditing);
        range.addEventListener("pointercancel", releaseEditing);
        range.addEventListener("blur", releaseEditing);
        range.addEventListener("change", async () => {
            releaseEditing();
            try {
                await fetch(form.action, {
                    method: "POST",
                    body: new FormData(form),
                    credentials: "same-origin",
                    redirect: "manual",
                });
                settleNowPlayingAfterChange();
            } catch {
                form.submit();
            }
        });
        form.addEventListener("submit", () => {
            range.value = String(Math.max(0, Math.min(100, Number(range.value))));
        });
    });
}

function setMuteButtonState(form, muted) {
    const input = form.querySelector('[name="muted"]');
    const button = form.querySelector("[data-mute-button]");
    const nextMuted = !muted;
    const label = muted ? tr("unmute", "Unmute") : tr("mute", "Mute");

    if (input) {
        input.value = nextMuted ? "true" : "false";
    }
    if (button) {
        button.classList.toggle("is-muted", muted);
        button.setAttribute("aria-label", label);
        const srOnly = button.querySelector(".sr-only");
        if (srOnly) {
            srOnly.textContent = label;
        }
    }

    const liveMuted = document.querySelector("[data-live-muted]");
    if (liveMuted) {
        liveMuted.hidden = !muted;
    }
}

function volumeLevelFromState(volumeState) {
    if (!volumeState || typeof volumeState !== "object") {
        return null;
    }
    const rawLevel = volumeState.target ?? volumeState.actual;
    const level = Number(rawLevel);
    if (!Number.isFinite(level)) {
        return null;
    }
    return String(Math.max(0, Math.min(100, Math.round(level))));
}

function setVolumeControlsState(volumeState) {
    if (!volumeState || typeof volumeState !== "object") {
        return;
    }

    const level = volumeLevelFromState(volumeState);
    document.querySelectorAll("[data-volume-form]").forEach((form) => {
        const range = form.querySelector("[data-volume-range]");
        if (range && level !== null && form.dataset.volumeUserEditing !== "true") {
            range.value = level;
        }
    });

    if ("muted" in volumeState) {
        document.querySelectorAll("[data-mute-form]").forEach((form) => {
            setMuteButtonState(form, Boolean(volumeState.muted));
        });
    }
}

function initMuteForms() {
    document.querySelectorAll("[data-mute-form]").forEach((form) => {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();

            const input = form.querySelector('[name="muted"]');
            const button = form.querySelector("[data-mute-button]");
            const requestedMuted = String(input?.value || "").toLowerCase() === "true";
            if (button) {
                button.disabled = true;
            }

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    body: new FormData(form),
                    credentials: "same-origin",
                    headers: {
                        "X-SoundCork-Request": "fetch",
                    },
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || !payload.ok) {
                    throw new Error(payload.error || tr("mute_update_failed", "Mute update failed"));
                }

                const muted =
                    typeof payload.muted === "boolean"
                        ? payload.muted
                        : Boolean(payload.volume_state?.muted ?? requestedMuted);
                setMuteButtonState(form, muted);
                renderNowPlayingPayload(payload);
                settleNowPlayingAfterChange();
            } catch (error) {
                showTransientMessage(
                    error.message || tr("mute_update_failed", "Mute update failed"),
                    "error"
                );
            } finally {
                if (button) {
                    button.disabled = false;
                }
            }
        });
    });
}

function currentPanelOrder() {
    const stack = document.querySelector("[data-panel-stack]");
    if (!stack) {
        return [];
    }
    return visibleReorderPanels(stack)
        .map((panel) => panel.dataset.panelId)
        .filter(Boolean);
}

function initBackupRestore() {
    document.querySelectorAll("[data-backup-form]").forEach((form) => {
        form.addEventListener("submit", () => {
            const input = form.querySelector("[data-panel-order-input]");
            if (input) {
                input.value = JSON.stringify(currentPanelOrder());
            }
        });
    });
}

function initConfirmableForms() {
    document.querySelectorAll("[data-confirm-form]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            const message = form.dataset.confirmMessage || "Are you sure?";
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });
}

function initPresetSearchAutosubmit() {
    const form = document.querySelector("[data-preset-search-form]");
    const input = form?.querySelector("[data-preset-search-input]");
    if (!form || !input) {
        return;
    }

    let timer = null;
    let composing = false;
    let lastSubmittedValue = input.value.trim();
    let activeSearch = null;
    let resultTimers = [];

    function clearSearchTimer() {
        window.clearTimeout(timer);
        timer = null;
    }

    function clearResultTimers() {
        resultTimers.forEach((timerId) => window.clearTimeout(timerId));
        resultTimers = [];
    }

    function setSearchMessage(selector, message) {
        const element = document.querySelector(selector);
        if (!element) {
            return;
        }
        element.textContent = message || "";
        element.hidden = !message;
    }

    function clearStationResults() {
        clearResultTimers();
        const results = document.querySelector("[data-station-results]");
        if (results) {
            results.replaceChildren();
            results.hidden = true;
        }
        setSearchMessage("[data-preset-search-empty]", "");
        setSearchMessage("[data-preset-search-error]", "");
    }

    function stationValue(station, key) {
        return String(station?.[key] || "");
    }

    function createStationResult(station) {
        const stationId = stationValue(station, "station_id");
        const stationName = stationValue(station, "name");
        const subtitle = stationValue(station, "subtitle");
        const imageUrl = stationValue(station, "image_url");

        const item = document.createElement("li");
        item.className = imageUrl ? "station-result has-art" : "station-result";
        item.dataset.stationId = stationId;
        item.dataset.stationName = stationName;
        item.dataset.stationImage = imageUrl;

        if (imageUrl) {
            const image = document.createElement("img");
            image.alt = "";
            image.draggable = false;
            image.dataset.optionalArt = "";
            setOptionalImage(image, imageUrl);
            item.appendChild(image);
        }

        const playForm = document.createElement("form");
        playForm.action = "/miniapp/play-tunein-station";
        playForm.method = "post";
        playForm.className = "station-play-form";
        const playId = document.createElement("input");
        playId.type = "hidden";
        playId.name = "station_id";
        playId.value = stationId;
        const playName = document.createElement("input");
        playName.type = "hidden";
        playName.name = "station_name";
        playName.value = stationName;
        const playImage = document.createElement("input");
        playImage.type = "hidden";
        playImage.name = "image_url";
        playImage.value = imageUrl;
        const playButton = document.createElement("button");
        playButton.type = "submit";
        playButton.className = "station-pick";
        playButton.setAttribute(
            "aria-label",
            tr("play_station", `Play ${stationName}`, { station_name: stationName })
        );
        const name = document.createElement("span");
        name.className = "station-name";
        name.textContent = stationName;
        playButton.appendChild(name);
        if (subtitle) {
            const subtitleElement = document.createElement("span");
            subtitleElement.className = "station-subtitle";
            subtitleElement.textContent = subtitle;
            playButton.appendChild(subtitleElement);
        }
        playForm.append(playId, playName, playImage, playButton);
        item.appendChild(playForm);

        return item;
    }

    function renderStationResults(results) {
        const list = document.querySelector("[data-station-results]");
        if (!list) {
            return;
        }
        clearResultTimers();
        list.replaceChildren();
        list.hidden = results.length === 0;
        setSearchMessage(
            "[data-preset-search-empty]",
            results.length === 0 ? tr("no_stations_found", "No stations found.") : ""
        );
        if (results.length === 0) {
            document.dispatchEvent(new CustomEvent("soundcork:stations-updated"));
            return;
        }

        const resultDelay = presetSearchResultDelayMs();
        results.forEach((station, index) => {
            const timerId = window.setTimeout(() => {
                const item = createStationResult(station);
                item.classList.add("is-arriving");
                list.appendChild(item);
                initOptionalArtImages(item);
                document.dispatchEvent(new CustomEvent("soundcork:stations-updated"));
            }, index * resultDelay);
            resultTimers.push(timerId);
        });
    }

    async function performSearch() {
        const value = input.value.trim();
        lastSubmittedValue = value;
        if (!value) {
            clearStationResults();
            return;
        }

        if (activeSearch) {
            activeSearch.abort();
        }
        activeSearch = new AbortController();
        const searchController = activeSearch;
        form.classList.add("is-searching");
        clearStationResults();
        setSearchMessage("[data-preset-search-error]", "");

        try {
            const params = new URLSearchParams({ preset_query: value });
            const response = await fetch(`/miniapp/search-stations?${params}`, {
                credentials: "same-origin",
                signal: searchController.signal,
            });
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || "Station search failed");
            }
            renderStationResults(Array.isArray(payload.results) ? payload.results : []);
        } catch (error) {
            if (error.name !== "AbortError") {
                clearStationResults();
                const message =
                    error.message === "Station search failed"
                        ? tr("station_search_failed", "Station search failed")
                        : error.message || tr("station_search_failed", "Station search failed");
                setSearchMessage("[data-preset-search-error]", message);
            }
        } finally {
            if (activeSearch === searchController) {
                activeSearch = null;
                form.classList.remove("is-searching");
            }
        }
    }

    function scheduleSearch() {
        clearSearchTimer();
        if (composing) {
            return;
        }

        const value = input.value.trim();
        const shouldSearch = value.length >= 2 || (value.length === 0 && lastSubmittedValue);
        if (!shouldSearch || value === lastSubmittedValue) {
            return;
        }

        timer = window.setTimeout(() => {
            performSearch();
        }, PRESET_SEARCH_DEBOUNCE_MS);
    }

    input.addEventListener("input", scheduleSearch);
    input.addEventListener("compositionstart", () => {
        composing = true;
        clearSearchTimer();
    });
    input.addEventListener("compositionend", () => {
        composing = false;
        scheduleSearch();
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        clearSearchTimer();
        performSearch();
    });
}

function initDismissibleMessages() {
    const messages = document.querySelectorAll(
        ".app-shell > .message.notice-message, .app-shell > .message.error-message"
    );
    if (messages.length === 0) {
        return;
    }

    messages.forEach((message) => {
        window.setTimeout(() => {
            message.classList.add("is-dismissing");
            window.setTimeout(() => {
                message.remove();
            }, 240);
        }, MESSAGE_DISMISS_DELAY_MS);
    });

    const params = new URLSearchParams(window.location.search);
    if (params.has("notice") || params.has("error")) {
        params.delete("notice");
        params.delete("error");
        const query = params.toString();
        window.history.replaceState(
            null,
            "",
            `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
        );
    }
}

function dismissMessageLater(message) {
    window.setTimeout(() => {
        message.classList.add("is-dismissing");
        window.setTimeout(() => {
            message.remove();
        }, 240);
    }, MESSAGE_DISMISS_DELAY_MS);
}

function showTransientMessage(message, type = "notice") {
    if (!message) {
        return;
    }
    const shell = document.querySelector(".app-shell");
    if (!shell) {
        return;
    }
    const element = document.createElement("div");
    element.className = `message ${
        type === "error" ? "error-message" : "notice-message"
    }`;
    element.setAttribute("role", type === "error" ? "alert" : "status");
    element.setAttribute("aria-live", "polite");
    element.textContent = message;
    shell.insertBefore(element, shell.firstElementChild);
    dismissMessageLater(element);
}

function createHiddenInput(name, value) {
    const input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value || "";
    return input;
}

function renderPresetSlot(slotElement, slot) {
    if (!slotElement || !slot) {
        return;
    }
    const number = Number(slot.number || slotElement.dataset.slot || 0);
    const preset = slot.preset || null;
    slotElement.dataset.slot = String(number);
    slotElement.dataset.presetId = preset?.id || "";
    slotElement.classList.toggle("empty", !preset);
    slotElement.replaceChildren();

    if (!preset) {
        const emptyCard = document.createElement("div");
        emptyCard.className = "preset-card empty-card";
        const numberLabel = document.createElement("span");
        numberLabel.className = "preset-number";
        numberLabel.textContent = String(number);
        const name = document.createElement("span");
        name.className = "preset-name";
        name.textContent = tr("empty", "Empty");
        emptyCard.append(numberLabel, name);
        slotElement.appendChild(emptyCard);
        return;
    }

    const form = document.createElement("form");
    form.action = "/miniapp/select-content-item";
    form.method = "post";
    form.className = "preset-form";
    form.append(
        createHiddenInput("content_item_id", preset.id || String(number)),
        createHiddenInput("content_item_name", preset.name || ""),
        createHiddenInput("content_item_art", preset.container_art || "")
    );

    const button = document.createElement("button");
    button.type = "submit";
    button.className = "preset-card";
    if (preset.container_art) {
        const image = document.createElement("img");
        image.alt = "";
        image.draggable = false;
        image.dataset.optionalArt = "";
        setOptionalImage(image, preset.container_art);
        button.appendChild(image);
    }
    const numberLabel = document.createElement("span");
    numberLabel.className = "preset-number";
    numberLabel.textContent = String(number);
    const name = document.createElement("span");
    name.className = "preset-name";
    name.textContent = preset.name || tr("selected_radio", "Selected radio");
    button.append(numberLabel, name);
    form.appendChild(button);
    slotElement.appendChild(form);
    initOptionalArtImages(slotElement);
}

function renderPresetSlots(slots) {
    const board = document.querySelector("[data-preset-board]");
    if (!board) {
        return;
    }
    slots.forEach((slot) => {
        const slotElement = Array.from(board.querySelectorAll(".preset-slot")).find(
            (item) => item.dataset.slot === String(slot.number)
        );
        renderPresetSlot(slotElement, slot);
    });
    syncPresetOrder(board);
}

function setText(selector, value) {
    const element = document.querySelector(selector);
    if (element) {
        element.textContent = value || "";
    }
}

function looksLikePlaceholderArt(url) {
    const value = String(url || "").trim().toLowerCase();
    if (!value) {
        return true;
    }
    if (
        value.includes("placeholder") ||
        value.includes("default-album-art") ||
        value.includes("tunein-default-album-art")
    ) {
        return true;
    }
    return value.includes("cdn-profiles.tunein.com") && /[?&]t=1(?:&|$)/.test(value);
}

function updateOptionalArtContainer(image) {
    const container = image.closest(".preset-card, .station-result, .now-art, .status-brand");
    if (container) {
        container.classList.toggle("has-hidden-art", image.hidden);
    }
}

function hideOptionalArt(image) {
    image.hidden = true;
    updateOptionalArtContainer(image);
}

function showOptionalArt(image) {
    image.hidden = false;
    updateOptionalArtContainer(image);
}

function prepareOptionalArt(image) {
    if (!(image instanceof HTMLImageElement)) {
        return;
    }
    if (looksLikePlaceholderArt(image.getAttribute("src") || image.currentSrc)) {
        hideOptionalArt(image);
        return;
    }
    image.addEventListener("error", () => {
        hideOptionalArt(image);
    });
    image.addEventListener("load", () => {
        if (image.naturalWidth <= 0 || image.naturalHeight <= 0) {
            hideOptionalArt(image);
        } else {
            showOptionalArt(image);
        }
    });
    if (image.complete) {
        if (image.naturalWidth <= 0 || image.naturalHeight <= 0) {
            hideOptionalArt(image);
        } else {
            showOptionalArt(image);
        }
    }
}

function initOptionalArtImages(root = document) {
    root.querySelectorAll("[data-optional-art]").forEach(prepareOptionalArt);
}

function setOptionalImage(image, src) {
    if (!(image instanceof HTMLImageElement)) {
        return;
    }
    if (looksLikePlaceholderArt(src)) {
        hideOptionalArt(image);
        image.removeAttribute("src");
        return;
    }
    showOptionalArt(image);
    if (image.getAttribute("src") !== src) {
        image.src = src;
    }
    prepareOptionalArt(image);
}

function updateLiveArt(src) {
    const status = document.querySelector("[data-live-status]");
    if (!status) {
        return;
    }

    let image = status.querySelector("[data-live-art]");
    if (looksLikePlaceholderArt(src)) {
        image?.remove();
        return;
    }

    if (!image) {
        image = document.createElement("img");
        image.className = "live-art";
        image.alt = "";
        image.dataset.liveArt = "";
        image.dataset.optionalArt = "";
        const dot = status.querySelector("[data-live-dot]");
        dot?.insertAdjacentElement("afterend", image);
    }
    setOptionalImage(image, src);
}

function liveMarqueeTextElement(container) {
    let text = container.querySelector(
        "[data-live-title-text], [data-live-station-text]"
    );
    if (text) {
        return text;
    }

    const value = container.textContent.trim();
    container.textContent = "";
    text = document.createElement("span");
    if (container.matches("[data-live-station]")) {
        text.className = "live-station-text";
        text.dataset.liveStationText = "";
    } else {
        text.className = "live-title-text";
        text.dataset.liveTitleText = "";
    }
    text.textContent = value;
    container.appendChild(text);
    return text;
}

function refreshLiveMarquee(container) {
    if (!container) {
        return;
    }

    const text = liveMarqueeTextElement(container);
    container.classList.remove("is-marquee");
    window.requestAnimationFrame(() => {
        if (!document.body.contains(container)) {
            return;
        }

        const previousMaxWidth = text.style.maxWidth;
        const previousAnimation = text.style.animation;
        const previousTransform = text.style.transform;
        text.style.maxWidth = "none";
        text.style.animation = "none";
        text.style.transform = "translateX(0)";

        const overflow = Math.ceil(text.scrollWidth - container.clientWidth);

        text.style.maxWidth = previousMaxWidth;
        text.style.animation = previousAnimation;
        text.style.transform = previousTransform;

        if (overflow <= LIVE_TITLE_OVERFLOW_TOLERANCE) {
            container.style.removeProperty("--live-marquee-distance");
            container.style.removeProperty("--live-marquee-duration");
            return;
        }

        const distance = overflow + 18;
        const duration = Math.max(7, Math.min(20, distance / 24 + 6));
        container.style.setProperty("--live-marquee-distance", `-${distance}px`);
        container.style.setProperty("--live-marquee-duration", `${duration.toFixed(1)}s`);
        container.classList.add("is-marquee");
    });
}

function refreshAllLiveMarquees() {
    document.querySelectorAll("[data-live-marquee], [data-live-title]").forEach(
        refreshLiveMarquee
    );
}

function setLiveTitle(value) {
    const container = document.querySelector("[data-live-title]");
    if (!container) {
        return;
    }

    const title = value || tr("no_radio_selected", "No radio selected");
    const text = liveMarqueeTextElement(container);
    if (text.textContent !== title) {
        text.textContent = title;
    }
    container.dataset.fullText = title;
    container.title = title;
    refreshLiveMarquee(container);
}

function setLiveStation(value) {
    const stationElement = document.querySelector("[data-live-station]");
    if (!stationElement) {
        return;
    }

    const station = value || tr("no_station_selected", "No station selected");
    const text = liveMarqueeTextElement(stationElement);
    if (text.textContent !== station) {
        text.textContent = station;
    }
    stationElement.dataset.fullText = station;
    stationElement.title = station;
    refreshLiveMarquee(stationElement);
}

function normalizedSourceKey(value) {
    return String(value || "").trim().replaceAll("_", " ").toLocaleLowerCase();
}

function sourceIsDisplayable(value) {
    const source = normalizedSourceKey(value);
    return Boolean(source && !["invalid source", "none", "unknown"].includes(source));
}

function playbackStateFromNow(now) {
    const playStatus = String(now?.play_status || "").toUpperCase();
    const hasMedia = Boolean(
        now?.item_name || now?.station_name || now?.track || now?.artist || now?.album
    );

    if (playStatus.includes("BUFFER")) {
        return "buffering";
    }
    if (playStatus.includes("PLAY")) {
        return "playing";
    }
    if (
        playStatus.includes("STOP") ||
        playStatus.includes("PAUSE") ||
        playStatus.includes("STANDBY") ||
        playStatus === "OFF"
    ) {
        return "stopped";
    }
    if (hasMedia && !normalizedSourceKey(now?.source).includes("invalid source")) {
        return "playing";
    }
    return "stopped";
}

function liveDetailText(now, station, fallback = "") {
    const normalize = (value) => String(value || "").trim().toLocaleLowerCase();
    const reportedStation = normalize(now?.station_name);
    const normalizedStation = normalize(station);
    const candidates = [
        now?.track,
        now?.artist,
        now?.album,
        sourceIsDisplayable(now?.source) ? now?.source : "",
    ].map(
        (value) => String(value || "").trim()
    );
    return (
        candidates.find(
            (value) =>
                value &&
                normalize(value) !== normalizedStation &&
                normalize(value) !== reportedStation
        ) ||
        (!sourceIsDisplayable(now?.source) && normalizedSourceKey(now?.source)
            ? tr("select_preset_or_search", "Select a preset or search for a station")
            : "") ||
        fallback ||
        tr("no_track_info", "No track info")
    );
}

function initLiveTitleMarquee() {
    const containers = document.querySelectorAll("[data-live-marquee], [data-live-title]");
    if (containers.length === 0) {
        return;
    }

    containers.forEach((container) => {
        const text = liveMarqueeTextElement(container);
        const title =
            text.textContent.trim() || tr("no_radio_selected", "No radio selected");
        container.dataset.fullText = title;
        container.title = title;
        refreshLiveMarquee(container);
    });

    let resizeTimer = null;
    window.addEventListener("resize", () => {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(refreshAllLiveMarquees, 120);
    });

    if (document.fonts?.ready) {
        document.fonts.ready.then(refreshAllLiveMarquees).catch(() => {});
    }
}

function updateLiveStatus(payload) {
    const status = document.querySelector("[data-live-status]");
    if (!status) {
        return;
    }
    if (!payload?.ok || !payload.now_playing) {
        return;
    }

    const now = payload?.now_playing || {};
    const volume = payload?.volume_state || {};
    const playbackState = playbackStateFromNow(now);
    const isBuffering = playbackState === "buffering";
    const isPlaying = playbackState === "playing";
    const stationElement = status.querySelector("[data-live-station]");
    const titleContainer = status.querySelector("[data-live-title]");
    const station =
        now.item_name ||
        now.station_name ||
        (!sourceIsDisplayable(now.source) && normalizedSourceKey(now.source)
            ? tr("radio_idle", "Ready")
            : "") ||
        stationElement?.textContent?.trim() ||
        tr("no_station_selected", "No station selected");
    const detail = liveDetailText(
        now,
        station,
        (isPlaying ? tr("playback_active", "Playing") : "") ||
            titleContainer?.dataset.fullText ||
            titleContainer?.textContent?.trim() ||
            tr("no_track_info", "No track info")
    );

    setLiveStation(station);
    setLiveTitle(detail);
    const dot = status.querySelector("[data-live-dot]");
    if (dot) {
        dot.classList.toggle("is-playing", isPlaying);
        dot.classList.toggle("is-buffering", isBuffering);
        dot.classList.toggle("is-stopped", !isPlaying && !isBuffering);
    }
    const muted = status.querySelector("[data-live-muted]");
    if (muted) {
        muted.hidden = !volume.muted;
    }
    updateLiveArt(now.art || "");
}

function nowPlayingTitle(now) {
    return now?.item_name || now?.station_name || now?.track || "";
}

function scheduledJobText(job) {
    if (!job) {
        return "";
    }
    return [
        `${job.label || ""} - ${job.run_at || ""}`.trim(),
        job.repeat_label || "",
        job.status || "",
    ]
        .filter(Boolean)
        .join(" - ");
}

function updateScheduledJobElement(element, job) {
    element.hidden = !job;
    if (!job) {
        return;
    }

    const label = element.querySelector("[data-job-label]");
    if (label) {
        label.textContent = scheduledJobText(job);
    }

    const pausedInput = element.querySelector("[data-job-paused-input]");
    if (pausedInput) {
        pausedInput.value = job.paused ? "false" : "true";
    }

    const toggleButton = element.querySelector("[data-job-toggle-button]");
    if (toggleButton) {
        toggleButton.textContent = job.paused
            ? tr("resume", "Resume")
            : tr("pause", "Pause");
    }
}

function scheduledJobItems(payload) {
    if (Array.isArray(payload?.job_list)) {
        return payload.job_list;
    }
    if (Array.isArray(payload?.jobs?.items)) {
        return payload.jobs.items;
    }
    return null;
}

function createJobActionForm(job, action) {
    const form = document.createElement("form");
    form.action = `/miniapp/${job.kind}/${action}`;
    form.method = "post";
    form.dataset.jobActionForm = "";

    const jobInput = document.createElement("input");
    jobInput.type = "hidden";
    jobInput.name = "job_id";
    jobInput.value = job.id || "";
    form.appendChild(jobInput);

    if (action === "toggle") {
        const pausedInput = document.createElement("input");
        pausedInput.type = "hidden";
        pausedInput.name = "paused";
        pausedInput.value = job.paused ? "false" : "true";
        pausedInput.dataset.jobPausedInput = "";
        form.appendChild(pausedInput);
    }

    const button = document.createElement("button");
    button.type = "submit";
    button.className = "compact-button";
    if (action === "toggle") {
        button.dataset.jobToggleButton = "";
        button.textContent = job.paused ? tr("resume", "Resume") : tr("pause", "Pause");
    } else {
        button.textContent = tr("remove", "Remove");
    }
    form.appendChild(button);
    return form;
}

function createScheduledJobElement(job) {
    const element = document.createElement("div");
    element.className = "job-pill";
    element.dataset.jobId = job.id || "";
    element.dataset.jobKind = job.kind || "";

    const label = document.createElement("span");
    label.dataset.jobLabel = "";
    label.textContent = scheduledJobText(job);

    const actions = document.createElement("div");
    actions.className = "job-actions";
    actions.append(createJobActionForm(job, "toggle"), createJobActionForm(job, "cancel"));

    element.append(label, actions);
    return element;
}

function renderScheduledJobs(payload) {
    if (!payload || !Object.prototype.hasOwnProperty.call(payload, "jobs")) {
        return;
    }

    const items = scheduledJobItems(payload);
    if (items) {
        document.querySelectorAll("[data-job-list-items]").forEach((container) => {
            container.replaceChildren(
                ...items.map((job) => createScheduledJobElement(job))
            );
        });
        initScheduledJobActionForms();

        document.querySelectorAll("[data-job-kind]").forEach((element) => {
            if (element.closest("[data-job-list-items]")) {
                return;
            }
            const job = items.find((item) => item.kind === element.dataset.jobKind);
            updateScheduledJobElement(element, job || null);
        });

        document.querySelectorAll("[data-job-list]").forEach((list) => {
            const dynamicContainer = list.querySelector("[data-job-list-items]");
            if (dynamicContainer) {
                list.hidden = items.length === 0;
                return;
            }
            const hasVisibleJob = Array.from(
                list.querySelectorAll("[data-job-kind]")
            ).some((element) => !element.hidden);
            list.hidden = !hasVisibleJob;
        });
        return;
    }

    const jobs = payload.jobs || {};
    document.querySelectorAll("[data-job-kind]").forEach((element) => {
        const job = jobs[element.dataset.jobKind];
        updateScheduledJobElement(element, job);
        return;
        element.hidden = !job;
        if (!job) {
            return;
        }

        const label = element.querySelector("[data-job-label]");
        if (label) {
            label.textContent = [
                `${job.label} - ${job.run_at}`,
                job.repeat_label || "",
                job.status || "",
            ]
                .filter(Boolean)
                .join(" · ");
        }
    });

    document.querySelectorAll("[data-job-list]").forEach((list) => {
        const hasVisibleJob = Array.from(
            list.querySelectorAll("[data-job-kind]")
        ).some((element) => !element.hidden);
        list.hidden = !hasVisibleJob;
    });
}

function initScheduledJobActionForms() {
    document.querySelectorAll("[data-job-action-form]").forEach((form) => {
        if (form.dataset.jobActionReady === "true") {
            return;
        }
        form.dataset.jobActionReady = "true";
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const submitButton = form.querySelector('button[type="submit"]');
            if (submitButton) {
                submitButton.disabled = true;
            }

            try {
                const response = await fetch(form.action, {
                    method: "POST",
                    body: new FormData(form),
                    credentials: "same-origin",
                    headers: {
                        "X-SoundCork-Request": "fetch",
                    },
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok || !payload.ok) {
                    throw new Error(payload.error || "Action failed");
                }
                renderScheduledJobs(payload);
            } catch (error) {
                showTransientMessage(error.message || "Action failed", "error");
            } finally {
                if (submitButton) {
                    submitButton.disabled = false;
                }
            }
        });
    });
}

function setInlineFormMessage(target, message, type = "notice") {
    if (!target) {
        return;
    }
    target.textContent = message || "";
    target.hidden = !message;
    target.classList.toggle("is-error", type === "error");
    target.classList.toggle("is-notice", type !== "error");
}

function sleepRepeatSelected(form) {
    return Boolean(
        form.querySelector("[data-repeat-daily]")?.checked ||
            (form.querySelector("[data-repeat-weekly]")?.checked &&
                Array.from(form.querySelectorAll("[data-repeat-day]")).some(
                    (input) => input.checked
                ))
    );
}

function repeatModeSelected(form) {
    return Boolean(
        form.querySelector("[data-repeat-daily]")?.checked ||
            form.querySelector("[data-repeat-weekly]")?.checked ||
            Array.from(form.querySelectorAll("[data-repeat-day]")).some(
                (input) => input.checked
            )
    );
}

function repeatWeeklyNeedsDay(form) {
    return Boolean(
        form.querySelector("[data-repeat-weekly]")?.checked &&
            !Array.from(form.querySelectorAll("[data-repeat-day]")).some(
                (input) => input.checked
            )
    );
}

function initSleepForm() {
    const form = document.querySelector("[data-sleep-form]");
    if (!form) {
        return;
    }

    const minutesInput = form.querySelector("[data-sleep-minutes]");
    const timeInput = form.querySelector("[data-sleep-time]");
    const repeatDaily = form.querySelector("[data-repeat-daily]");
    const repeatWeekly = form.querySelector("[data-repeat-weekly]");
    const repeatDays = Array.from(form.querySelectorAll("[data-repeat-day]"));
    const repeatControls = Array.from(form.querySelectorAll("[data-repeat-control]"));
    const weeklyDays = form.querySelector("[data-repeat-weekly-days]");
    const message = form.querySelector("[data-sleep-form-message]");

    function clearRepeat() {
        if (repeatDaily) {
            repeatDaily.checked = false;
        }
        if (repeatWeekly) {
            repeatWeekly.checked = false;
        }
        repeatDays.forEach((input) => {
            input.checked = false;
        });
    }

    function setRepeatDisabled(disabled) {
        repeatControls.forEach((control) => {
            control.classList.toggle("is-disabled", disabled);
        });
        if (repeatDaily) {
            repeatDaily.disabled = disabled;
        }
        if (repeatWeekly) {
            repeatWeekly.disabled = disabled || Boolean(repeatDaily?.checked);
        }
        weeklyDays?.classList.toggle(
            "is-disabled",
            disabled || !repeatWeekly?.checked || Boolean(repeatDaily?.checked)
        );
        repeatDays.forEach((input) => {
            input.disabled =
                disabled ||
                Boolean(repeatDaily?.checked) ||
                !Boolean(repeatWeekly?.checked);
        });
    }

    function updateCompatibility(options = {}) {
        const hasMinutes = Boolean(minutesInput?.value.trim());
        const hasTime = Boolean(timeInput?.value.trim());
        const hasRepeat = repeatModeSelected(form);

        if (hasMinutes) {
            if (timeInput) {
                timeInput.value = "";
                timeInput.disabled = true;
            }
            clearRepeat();
            setRepeatDisabled(true);
            if (minutesInput) {
                minutesInput.disabled = false;
            }
            if (options.showHint) {
                setInlineFormMessage(
                    message,
                    tr(
                        "sleep_minutes_one_time_only",
                        "Minute sleep timers are one-time only."
                    ),
                    "notice"
                );
            }
            return;
        }

        if (timeInput) {
            timeInput.disabled = false;
        }
        setRepeatDisabled(false);
        if (minutesInput) {
            minutesInput.disabled = hasTime || hasRepeat;
        }
        if (!options.keepMessage) {
            setInlineFormMessage(message, "");
        }
    }

    minutesInput?.addEventListener("input", () =>
        updateCompatibility({ showHint: true })
    );
    timeInput?.addEventListener("input", () => updateCompatibility());
    repeatDaily?.addEventListener("change", () => {
        if (repeatDaily.checked) {
            if (repeatWeekly) {
                repeatWeekly.checked = false;
            }
            repeatDays.forEach((input) => {
                input.checked = false;
            });
        }
        updateCompatibility();
    });
    repeatWeekly?.addEventListener("change", () => {
        if (repeatWeekly.checked && repeatDaily) {
            repeatDaily.checked = false;
        }
        if (!repeatWeekly.checked) {
            repeatDays.forEach((input) => {
                input.checked = false;
            });
        }
        updateCompatibility();
    });
    repeatDays.forEach((input) => {
        input.addEventListener("change", () => {
            if (input.checked && repeatWeekly) {
                repeatWeekly.checked = true;
            }
            if (input.checked && repeatDaily) {
                repeatDaily.checked = false;
            }
            updateCompatibility();
        });
    });
    updateCompatibility();

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        updateCompatibility({ keepMessage: true });

        const hasMinutes = Boolean(minutesInput?.value.trim());
        const hasTime = Boolean(timeInput?.value.trim());
        const hasRepeat = repeatModeSelected(form);

        if (repeatWeeklyNeedsDay(form)) {
            setInlineFormMessage(
                message,
                tr(
                    "repeat_weekly_needs_day",
                    "Repeat weekly needs at least one weekday."
                ),
                "error"
            );
            return;
        }
        if (hasRepeat && !hasTime) {
            setInlineFormMessage(
                message,
                tr(
                    "sleep_repeat_needs_clock_time",
                    "Repeating sleep needs a clock time."
                ),
                "error"
            );
            return;
        }
        if (!hasMinutes && !hasTime) {
            setInlineFormMessage(
                message,
                tr("invalid_sleep_timer", "Invalid sleep timer"),
                "error"
            );
            return;
        }

        const submitButton = form.querySelector('button[type="submit"]');
        if (submitButton) {
            submitButton.disabled = true;
        }

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                credentials: "same-origin",
                headers: {
                    "X-SoundCork-Request": "fetch",
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                throw new Error(
                    payload.error || tr("invalid_sleep_timer", "Invalid sleep timer")
                );
            }
            renderScheduledJobs(payload);
            setInlineFormMessage(message, payload.notice || scheduledJobText(payload.job));
            form.reset();
            updateCompatibility({ keepMessage: true });
        } catch (error) {
            setInlineFormMessage(
                message,
                error.message || tr("invalid_sleep_timer", "Invalid sleep timer"),
                "error"
            );
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
            }
        }
    });
}

function initAlarmForm() {
    const form = document.querySelector("[data-alarm-form]");
    if (!form) {
        return;
    }

    const message = form.querySelector("[data-alarm-form-message]");
    const repeatDaily = form.querySelector("[data-repeat-daily]");
    const repeatWeekly = form.querySelector("[data-repeat-weekly]");
    const repeatDays = Array.from(form.querySelectorAll("[data-repeat-day]"));
    const weeklyDays = form.querySelector("[data-repeat-weekly-days]");

    function updateAlarmRepeatControls() {
        if (repeatDaily?.checked) {
            if (repeatWeekly) {
                repeatWeekly.checked = false;
            }
            repeatDays.forEach((input) => {
                input.checked = false;
            });
        }
        weeklyDays?.classList.toggle(
            "is-disabled",
            !Boolean(repeatWeekly?.checked) || Boolean(repeatDaily?.checked)
        );
        repeatDays.forEach((input) => {
            input.disabled =
                Boolean(repeatDaily?.checked) || !Boolean(repeatWeekly?.checked);
        });
    }

    repeatDaily?.addEventListener("change", () => {
        updateAlarmRepeatControls();
    });
    repeatWeekly?.addEventListener("change", () => {
        if (repeatWeekly.checked && repeatDaily) {
            repeatDaily.checked = false;
        }
        if (!repeatWeekly.checked) {
            repeatDays.forEach((input) => {
                input.checked = false;
            });
        }
        updateAlarmRepeatControls();
    });
    repeatDays.forEach((input) => {
        input.addEventListener("change", () => {
            if (input.checked && repeatWeekly) {
                repeatWeekly.checked = true;
            }
            if (input.checked && repeatDaily) {
                repeatDaily.checked = false;
            }
            updateAlarmRepeatControls();
        });
    });
    updateAlarmRepeatControls();

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        setInlineFormMessage(message, "");

        if (repeatWeeklyNeedsDay(form)) {
            setInlineFormMessage(
                message,
                tr(
                    "repeat_weekly_needs_day",
                    "Repeat weekly needs at least one weekday."
                ),
                "error"
            );
            return;
        }

        const submitButton = form.querySelector('button[type="submit"]');
        if (submitButton) {
            submitButton.disabled = true;
        }

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                credentials: "same-origin",
                headers: {
                    "X-SoundCork-Request": "fetch",
                },
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || !payload.ok) {
                throw new Error(
                    payload.error || tr("invalid_alarm_time", "Invalid alarm time")
                );
            }
            renderScheduledJobs(payload);
            setInlineFormMessage(message, payload.notice || scheduledJobText(payload.job));
            form.reset();
            updateAlarmRepeatControls();
        } catch (error) {
            setInlineFormMessage(
                message,
                error.message || tr("invalid_alarm_time", "Invalid alarm time"),
                "error"
            );
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
            }
        }
    });
}

function renderNowPlayingPayload(payload, options = {}) {
    const renderJobs = options.renderJobs !== false;
    const renderVolume = options.renderVolume !== false;
    const renderLive = options.renderLive !== false;
    const renderCard = options.renderCard !== false;

    if (renderJobs) {
        renderScheduledJobs(payload);
    }
    if (renderVolume) {
        setVolumeControlsState(payload?.volume_state);
    }

    const card = renderCard ? document.querySelector("[data-now-playing-card]") : null;
    const status = renderLive ? document.querySelector("[data-live-status]") : null;
    if (!card && !status) {
        return;
    }

    const title = nowPlayingTitle(payload?.now_playing);
    if (
        !options.allowOptimisticOverride &&
        optimisticPlaybackName &&
        Date.now() < optimisticPlaybackUntil &&
        title &&
        title !== optimisticPlaybackName
    ) {
        return;
    }

    if (renderLive) {
        updateLiveStatus(payload);
    }
    if (!payload?.ok || !payload.now_playing) {
        return;
    }
    const now = payload.now_playing;
    if (card) {
        setText("[data-now-playing-status]", now.play_status || tr("unknown", "Unknown"));
        const displayTitle = now.item_name || now.station_name || now.track || "";
        const displayTrack =
            now.track && now.track !== displayTitle && now.track !== now.station_name
                ? now.track
                : "";
        const displayMeta =
            now.artist ||
            (sourceIsDisplayable(now.source) && now.source !== displayTitle
                ? now.source
                : "");
        setText("[data-now-playing-title]", displayTitle);
        setText("[data-now-playing-track]", displayTrack);
        setText("[data-now-playing-meta]", displayMeta);

        const artTarget = document.querySelector("[data-now-playing-art]");
        if (artTarget) {
            if (artTarget.tagName.toLowerCase() === "img") {
                setOptionalImage(artTarget, now.art || "");
            } else {
                if (looksLikePlaceholderArt(now.art)) {
                    return;
                }
                const image = document.createElement("img");
                image.alt = "";
                image.dataset.nowPlayingArt = "";
                image.dataset.optionalArt = "";
                setOptionalImage(image, now.art);
                artTarget.replaceWith(image);
            }
        }
    }
}

async function refreshNowPlaying(options = {}) {
    try {
        const response = await fetch("/miniapp/now-playing", {
            credentials: "same-origin",
        });
        renderNowPlayingPayload(await response.json(), options);
    } catch {
        // The next refresh can recover.
    }
}

function optimisticNowPlaying(name, volumeState = null, art = "") {
    optimisticPlaybackName = name;
    optimisticPlaybackUntil = Date.now() + PLAYBACK_OPTIMISTIC_WINDOW_MS;
    renderNowPlayingPayload({
        ok: true,
        now_playing: {
            item_name: name,
            station_name: name,
            track: "",
            artist: "",
            source: "SoundCork",
            art,
            play_status: "PLAY_STATE",
        },
        volume_state: volumeState,
    }, { allowOptimisticOverride: true });
}

function settleNowPlayingAfterChange() {
    [1200, 3500, 7000].forEach((delay) => {
        window.setTimeout(refreshNowPlaying, delay);
    });
}

function initNowPlayingRefresh() {
    const card = document.querySelector("[data-now-playing-card]");
    const status = document.querySelector("[data-live-status]");
    const volumeControls = document.querySelector("[data-volume-form], [data-mute-form]");
    if (!card && !status && !volumeControls) {
        return;
    }
    refreshNowPlaying();
    if (card || status) {
        setInterval(
            () => refreshNowPlaying({ renderVolume: false }),
            nowPlayingPollIntervalMs()
        );
    }
    if (volumeControls) {
        setInterval(
            () =>
                refreshNowPlaying({
                    renderCard: false,
                    renderJobs: false,
                    renderLive: false,
                    renderVolume: true,
                }),
            volumePollIntervalMs()
        );
    }
}

function initPlaybackForms() {
    document.addEventListener("submit", async (event) => {
        const form = event.target;
        if (
            !(form instanceof HTMLFormElement) ||
            !form.matches(".preset-form, .station-play-form")
        ) {
            return;
        }

        event.preventDefault();
        const nameInput = form.querySelector(
            '[name="content_item_name"], [name="station_name"]'
        );
        const fallbackName =
            nameInput?.value ||
            form.querySelector(".preset-name, .station-name")?.textContent?.trim() ||
            tr("selected_radio", "Selected radio");
        const artInput = form.querySelector('[name="content_item_art"], [name="image_url"]');
        const fallbackArt =
            artInput?.value ||
            form.querySelector("img[data-optional-art]:not([hidden])")?.currentSrc ||
            "";

        optimisticNowPlaying(fallbackName, null, fallbackArt);

        try {
            const response = await fetch(form.action, {
                method: "POST",
                body: new FormData(form),
                credentials: "same-origin",
                headers: {
                    "X-SoundCork-Request": "fetch",
                },
            });
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || tr("playback_failed", "Playback failed"));
            }
            optimisticNowPlaying(
                payload.selected_content_item_name || fallbackName,
                payload.volume_state || null,
                payload.now_playing?.art || fallbackArt
            );
            renderNowPlayingPayload(payload);
            settleNowPlayingAfterChange();
        } catch {
            HTMLFormElement.prototype.submit.call(form);
        }
    });
}

function exactPresetSlotAtPoint(board, clientX, clientY) {
    const element = document.elementFromPoint(clientX, clientY);
    const slot = element?.closest(".preset-slot");
    return slot && slot.parentElement === board ? slot : null;
}

function presetReorderItems(board) {
    return Array.from(board.children).filter(
        (item) =>
            item.classList.contains("preset-source-placeholder") ||
            (item.classList.contains("preset-slot") && !item.hidden)
    );
}

function rowCenter(rect) {
    return rect.top + rect.height / 2;
}

function samePresetRow(firstRect, secondRect) {
    return Math.abs(rowCenter(firstRect) - rowCenter(secondRect)) < 18;
}

function presetInsertionIndexAtPoint(board, clientX, clientY) {
    const items = presetReorderItems(board);
    if (items.length === 0) {
        return null;
    }

    const boardRect = board.getBoundingClientRect();
    if (
        clientX < boardRect.left - 18 ||
        clientX > boardRect.right + 18 ||
        clientY < boardRect.top - 18 ||
        clientY > boardRect.bottom + 18
    ) {
        return null;
    }

    const gapPadding = 18;
    const rowPadding = 8;

    const firstRect = items[0].getBoundingClientRect();
    const lastRect = items[items.length - 1].getBoundingClientRect();
    const inFirstRow =
        clientY >= firstRect.top - rowPadding && clientY <= firstRect.bottom + rowPadding;
    const inLastRow =
        clientY >= lastRect.top - rowPadding && clientY <= lastRect.bottom + rowPadding;

    if (
        inFirstRow &&
        clientX >= boardRect.left - gapPadding &&
        clientX <= firstRect.left + gapPadding
    ) {
        return 0;
    }

    if (
        inLastRow &&
        clientX >= lastRect.right - gapPadding &&
        clientX <= boardRect.right + gapPadding
    ) {
        return items.length;
    }

    for (let index = 1; index < items.length; index += 1) {
        const previousRect = items[index - 1].getBoundingClientRect();
        const nextRect = items[index].getBoundingClientRect();
        if (samePresetRow(previousRect, nextRect)) {
            const sameRow =
                clientY >= Math.min(previousRect.top, nextRect.top) - rowPadding &&
                clientY <= Math.max(previousRect.bottom, nextRect.bottom) + rowPadding;
            if (
                sameRow &&
                clientX >= previousRect.right - gapPadding &&
                clientX <= nextRect.left + gapPadding
            ) {
                return index;
            }
            continue;
        }

        if (
            clientY >= previousRect.bottom - gapPadding &&
            clientY <= nextRect.top + gapPadding &&
            clientX >= boardRect.left - gapPadding &&
            clientX <= boardRect.right + gapPadding
        ) {
            return index;
        }
    }

    return null;
}

function currentPresetOrder(board) {
    return Array.from(board.querySelectorAll(".preset-slot"))
        .filter((slot) => !slot.hidden)
        .map((slot) => slot.dataset.presetId || "");
}

function positionPresetInsertionIndicator(indicator, board, targetIndex, clientX = null) {
    const items = presetReorderItems(board);
    if (targetIndex === null || items.length === 0) {
        indicator.hidden = true;
        return;
    }

    const firstRect = items[0].getBoundingClientRect();
    const previous = targetIndex > 0 ? items[targetIndex - 1] : null;
    const next = targetIndex < items.length ? items[targetIndex] : null;
    const previousRect = previous?.getBoundingClientRect();
    const nextRect = next?.getBoundingClientRect();

    indicator.hidden = false;
    indicator.classList.remove("is-horizontal", "is-vertical");

    let anchorRect = nextRect || previousRect || firstRect;
    let left = nextRect ? nextRect.left - 6 : anchorRect.right + 6;
    if (previousRect && nextRect && Number.isFinite(clientX)) {
        const previousDistance = Math.abs(clientX - previousRect.right);
        const nextDistance = Math.abs(clientX - nextRect.left);
        if (previousDistance <= nextDistance) {
            anchorRect = previousRect;
            left = previousRect.right + 6;
        } else {
            anchorRect = nextRect;
            left = nextRect.left - 6;
        }
    } else if (nextRect) {
        anchorRect = nextRect;
        left = nextRect.left - 6;
    } else if (previousRect) {
        anchorRect = previousRect;
        left = previousRect.right + 6;
    }

    indicator.classList.add("is-vertical");
    indicator.style.left = `${left}px`;
    indicator.style.top = `${anchorRect.top}px`;
    indicator.style.height = `${anchorRect.height}px`;
    indicator.style.width = "";
}

async function autosavePresetOrder(board) {
    const form = document.getElementById("preset-reorder-form");
    if (!form) {
        return;
    }
    syncPresetOrder(board);
    const button = form.querySelector("[data-preset-order-submit]");
    if (button) {
        setPresetOrderStatus(button, tr("saving", "Saving..."));
        button.disabled = true;
    }

    try {
        const response = await fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            credentials: "same-origin",
            headers: {
                "X-SoundCork-Request": "fetch",
            },
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
            throw new Error("Preset sync failed");
        }
        if (Array.isArray(payload.preset_slots) && payload.preset_slots.length) {
            renderPresetSlots(payload.preset_slots);
        } else {
            commitPresetOrderToRadioSlots(board);
        }
        document.dispatchEvent(new CustomEvent("soundcork:presets-updated"));
        if (button) {
            setPresetOrderStatus(button, tr("synced", "Synced"));
        }
    } catch {
        if (button) {
            setPresetOrderStatus(button, tr("sync_failed", "Sync Failed"));
        } else {
            form.submit();
            return;
        }
    } finally {
        if (button) {
            window.setTimeout(() => {
                setPresetOrderStatus(button, tr("phone_to_radio", "Phone to radio"));
                button.disabled = false;
            }, 1200);
        }
    }
}

function setPresetOrderStatus(button, value) {
    if (!button) {
        return;
    }

    const status = button.querySelector("[data-preset-order-status]");
    if (status) {
        status.textContent = value;
        return;
    }
    button.textContent = value;
}

function initPresetSyncPopover() {
    const opener = document.querySelector("[data-sync-dialog-open]");
    const dialog = document.querySelector("[data-sync-dialog]");
    if (!opener || !dialog) {
        return;
    }

    let previousFocus = null;

    function openDialog() {
        previousFocus = document.activeElement;
        dialog.hidden = false;
        opener.setAttribute("aria-expanded", "true");
        const firstButton = dialog.querySelector(".sync-choice-button");
        firstButton?.focus();
    }

    function closeDialog(restoreFocus = true) {
        if (dialog.hidden) {
            return;
        }
        dialog.hidden = true;
        opener.setAttribute("aria-expanded", "false");
        if (restoreFocus && previousFocus instanceof HTMLElement) {
            previousFocus.focus();
        }
    }

    opener.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (dialog.hidden) {
            openDialog();
        } else {
            closeDialog(false);
        }
    });

    dialog.querySelectorAll("[data-sync-dialog-close]").forEach((button) => {
        button.addEventListener("click", () => closeDialog());
    });

    document.addEventListener("click", (event) => {
        if (
            dialog.hidden ||
            dialog.contains(event.target) ||
            opener.contains(event.target)
        ) {
            return;
        }
        closeDialog(false);
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeDialog();
        }
    });
}

function initPresetBoard() {
    const board = document.querySelector("[data-preset-board]");
    if (!board) {
        return;
    }

    let pending = null;
    let drag = null;
    let suppressClick = false;

    board.addEventListener("dragstart", (event) => {
        if (event.target.closest(".preset-card")) {
            event.preventDefault();
        }
    });

    board.addEventListener("contextmenu", (event) => {
        if (event.target.closest(".preset-card")) {
            event.preventDefault();
        }
    });

    function cleanupPending() {
        if (!pending) {
            return;
        }
        window.clearTimeout(pending.longPressTimer);
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerUp);
        window.removeEventListener("pointercancel", handlePointerCancel);
        pending = null;
    }

    function beginPresetDrag(clientX, clientY) {
        if (!pending || drag || pending.cancelled) {
            return;
        }

        const rect = pending.slot.getBoundingClientRect();
        const initialOrder = currentPresetOrder(board);
        const placeholder = document.createElement("li");
        placeholder.className = "preset-source-placeholder";
        placeholder.setAttribute("aria-hidden", "true");
        board.insertBefore(placeholder, pending.slot);

        const ghost = createDragGhost(pending.slot, "preset-drag-ghost", rect);
        ghost.style.opacity = String(presetDragOpacity());
        const indicator = document.createElement("div");
        indicator.className = "preset-insertion-indicator";
        indicator.hidden = true;
        document.body.appendChild(indicator);
        pending.slot.hidden = true;
        pending.slot.classList.add("is-preset-source");
        board.classList.add("is-preset-reordering");
        document.body.classList.add("is-dragging-preset");
        try {
            pending.slot.setPointerCapture(pending.pointerId);
        } catch {
            // Some browsers do not allow late capture; window listeners still track the drag.
        }

        drag = {
            type: "preset",
            slot: pending.slot,
            placeholder,
            indicator,
            ghost,
            initialOrder,
            pointerId: pending.pointerId,
            offsetX: pending.startX - rect.left,
            offsetY: pending.startY - rect.top,
            targetIndex: null,
        };
        moveDragGhost(ghost, { clientX, clientY }, drag.offsetX, drag.offsetY);
        suppressClick = true;
    }

    function finishPresetDrag(commit) {
        if (!drag || drag.type !== "preset") {
            return;
        }

        const targetIndex = drag.targetIndex;
        const items = presetReorderItems(board);
        const referenceItem =
            Number.isInteger(targetIndex) && targetIndex < items.length
                ? items[targetIndex]
                : null;
        const shouldCommit = commit && Number.isInteger(targetIndex);

        if (shouldCommit) {
            if (referenceItem && referenceItem !== drag.placeholder) {
                board.insertBefore(drag.slot, referenceItem);
            } else if (referenceItem === drag.placeholder) {
                board.insertBefore(drag.slot, drag.placeholder);
            } else {
                board.appendChild(drag.slot);
            }
        }
        drag.slot.hidden = false;
        drag.slot.classList.remove("is-preset-source");
        drag.placeholder.remove();
        drag.indicator.remove();
        drag.ghost.remove();
        board.classList.remove("is-preset-reordering");
        document.body.classList.remove("is-dragging-preset");

        if (shouldCommit && currentPresetOrder(board).join("|") !== drag.initialOrder.join("|")) {
            autosavePresetOrder(board);
        } else {
            syncPresetOrder(board);
        }
        drag = null;
    }

    function handlePointerMove(event) {
        if (pending && event.pointerId === pending.pointerId) {
            pending.lastX = event.clientX;
            pending.lastY = event.clientY;

            if (!drag && !pending.cancelled) {
                const distance = dragDistance(
                    pending.startX,
                    pending.startY,
                    event.clientX,
                    event.clientY
                );
                if (distance >= PRESET_LONG_PRESS_CANCEL_DISTANCE) {
                    pending.cancelled = true;
                    window.clearTimeout(pending.longPressTimer);
                    suppressClick = true;
                    return;
                }
            }
        }

        if (!drag || drag.pointerId !== event.pointerId) {
            return;
        }

        event.preventDefault();
        moveDragGhost(drag.ghost, event, drag.offsetX, drag.offsetY);
        drag.targetIndex = presetInsertionIndexAtPoint(
            board,
            event.clientX,
            event.clientY
        );
        positionPresetInsertionIndicator(drag.indicator, board, drag.targetIndex, event.clientX);
        autoScrollForPointer(event.clientY);
    }

    function handlePointerUp(event) {
        if (!pending || event.pointerId !== pending.pointerId) {
            return;
        }
        const wasDragging = Boolean(drag);
        const wasCancelled = Boolean(pending.cancelled);
        if (wasDragging) {
            event.preventDefault();
        } else if (wasCancelled) {
            suppressClick = true;
        }
        finishPresetDrag(wasDragging);
        cleanupPending();
        window.setTimeout(() => {
            suppressClick = false;
        }, wasCancelled ? 350 : 0);
    }

    function handlePointerCancel(event) {
        if (!pending || event.pointerId !== pending.pointerId) {
            return;
        }
        finishPresetDrag(false);
        cleanupPending();
        suppressClick = false;
    }

    board.addEventListener(
        "click",
        (event) => {
            if (!suppressClick) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            suppressClick = false;
        },
        true
    );

    function bindPresetSlot(slot) {
        if (!slot.dataset.presetId || slot.dataset.presetDragReady === "true") {
            return;
        }
        slot.dataset.presetDragReady = "true";
        slot.addEventListener("pointerdown", (event) => {
            if (!slot.dataset.presetId || !isPrimaryPointer(event) || pending || drag) {
                return;
            }
            pending = {
                slot,
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                lastX: event.clientX,
                lastY: event.clientY,
                cancelled: false,
                longPressTimer: window.setTimeout(() => {
                    if (!pending || pending.pointerId !== event.pointerId) {
                        return;
                    }
                    beginPresetDrag(pending.lastX, pending.lastY);
                }, presetLongPressDelayMs()),
            };
            window.addEventListener("pointermove", handlePointerMove, {
                passive: false,
            });
            window.addEventListener("pointerup", handlePointerUp);
            window.addEventListener("pointercancel", handlePointerCancel);
        });
    }

    function bindPresetSlots() {
        board.querySelectorAll(".preset-slot").forEach(bindPresetSlot);
    }

    bindPresetSlots();
    document.addEventListener("soundcork:presets-updated", bindPresetSlots);

    document.getElementById("preset-reorder-form")?.addEventListener("submit", () => {
        syncPresetOrder(board);
    });

    syncPresetOrder(board);
}

function initStationDrag() {
    const board = document.querySelector("[data-preset-board]");
    if (!board) {
        return;
    }

    let pending = null;
    let drag = null;
    let suppressClick = false;

    document.addEventListener("dragstart", (event) => {
        if (event.target.closest(".station-result")) {
            event.preventDefault();
        }
    });

    document.addEventListener("contextmenu", (event) => {
        if (event.target.closest(".station-result")) {
            event.preventDefault();
        }
    });

    function cleanupPending() {
        if (!pending) {
            return;
        }
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerUp);
        window.removeEventListener("pointercancel", handlePointerCancel);
        pending = null;
    }

    function stationPayload(station) {
        return {
            stationId: station.dataset.stationId,
            stationName: station.dataset.stationName,
            stationImage: station.dataset.stationImage,
        };
    }

    function beginStationDrag(event) {
        const rect = pending.station.getBoundingClientRect();
        const ghost = createDragGhost(pending.station, "station-drag-ghost", rect);
        pending.station.classList.add("is-station-source");
        document.body.classList.add("is-dragging-station");
        try {
            pending.station.setPointerCapture(pending.pointerId);
        } catch {
            // Some browsers do not allow late capture; window listeners still track the drag.
        }
        drag = {
            type: "station",
            station: pending.station,
            payload: stationPayload(pending.station),
            ghost,
            pointerId: pending.pointerId,
            offsetX: pending.startX - rect.left,
            offsetY: pending.startY - rect.top,
            targetSlot: null,
        };
        moveDragGhost(ghost, event, drag.offsetX, drag.offsetY);
        suppressClick = true;
    }

    function finishStationDrag(commit) {
        if (!drag || drag.type !== "station") {
            return;
        }

        const targetSlot = drag.targetSlot;
        const payload = drag.payload;
        drag.station.classList.remove("is-station-source");
        drag.ghost.remove();
        clearSlotDropTargets();
        document.body.classList.remove("is-dragging-station");
        drag = null;

        if (commit && targetSlot) {
            submitStationToSlot(payload, targetSlot.dataset.slot);
        }
    }

    function handlePointerMove(event) {
        if (pending && event.pointerId === pending.pointerId) {
            if (
                !drag &&
                dragDistance(
                    pending.startX,
                    pending.startY,
                    event.clientX,
                    event.clientY
                ) >= DRAG_START_DISTANCE
            ) {
                beginStationDrag(event);
            }
        }

        if (!drag || drag.pointerId !== event.pointerId) {
            return;
        }

        event.preventDefault();
        moveDragGhost(drag.ghost, event, drag.offsetX, drag.offsetY);
        drag.targetSlot = exactPresetSlotAtPoint(board, event.clientX, event.clientY);
        markSlotDropTarget(drag.targetSlot);
        autoScrollForPointer(event.clientY);
    }

    function handlePointerUp(event) {
        if (!pending || event.pointerId !== pending.pointerId) {
            return;
        }
        const wasDragging = Boolean(drag);
        if (wasDragging) {
            event.preventDefault();
        }
        finishStationDrag(wasDragging);
        cleanupPending();
        window.setTimeout(() => {
            suppressClick = false;
        }, 0);
    }

    function handlePointerCancel(event) {
        if (!pending || event.pointerId !== pending.pointerId) {
            return;
        }
        finishStationDrag(false);
        cleanupPending();
        suppressClick = false;
    }

    function bindStation(station) {
        if (station.dataset.stationDragReady === "true") {
            return;
        }
        station.dataset.stationDragReady = "true";

        station.addEventListener(
            "click",
            (event) => {
                if (suppressClick) {
                    event.preventDefault();
                    event.stopPropagation();
                    suppressClick = false;
                    return;
                }

                if (
                    event.target.closest(
                        ".station-play-form, a, input, select, textarea"
                    )
                ) {
                    return;
                }

                const playForm = station.querySelector(".station-play-form");
                if (playForm) {
                    event.preventDefault();
                    playForm.requestSubmit();
                }
            },
            false
        );

        station.addEventListener("pointerdown", (event) => {
            const stationPlayButton = event.target.closest(".station-pick");
            const blockedControl = event.target.closest(
                "button, a, input, select, textarea"
            );
            if (
                !isPrimaryPointer(event) ||
                pending ||
                drag ||
                (blockedControl && blockedControl !== stationPlayButton)
            ) {
                return;
            }

            pending = {
                station,
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
            };
            window.addEventListener("pointermove", handlePointerMove, {
                passive: false,
            });
            window.addEventListener("pointerup", handlePointerUp);
            window.addEventListener("pointercancel", handlePointerCancel);
        });
    }

    document.querySelectorAll(".station-result").forEach(bindStation);
    document.addEventListener("soundcork:stations-updated", () => {
        document.querySelectorAll(".station-result").forEach(bindStation);
    });
}

function initPasswordToggles() {
    document.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
        const form = toggle.closest("form");
        const passwordInput = form?.querySelector("[data-password-input]");
        if (!passwordInput) {
            return;
        }

        const updatePasswordVisibility = () => {
            passwordInput.type = toggle.checked ? "text" : "password";
        };
        toggle.addEventListener("change", updatePasswordVisibility);
        updatePasswordVisibility();
    });
}

function initMillisecondRangeFields() {
    document.querySelectorAll("[data-ms-range]").forEach((input) => {
        const output = input
            .closest(".field")
            ?.querySelector("[data-ms-range-value]");
        if (!output) {
            return;
        }
        const updateOutput = () => {
            output.textContent = `${(Number(input.value) / 1000).toFixed(1)}s`;
        };
        input.addEventListener("input", updateOutput);
        updateOutput();
    });
}

document.addEventListener("DOMContentLoaded", () => {
    applyPanelOrderFromQuery();
    applyAllPanelLabels();
    applyTimerSectionLabels();
    applyAllPanelStyles();
    applyPresetThumbnailSize();
    applyTimerJobVisibleCount();
    initTopbarReorder();
    initOptionalArtImages();
    initPanelReorder();
    initVolumeForms();
    initMuteForms();
    initBackupRestore();
    initConfirmableForms();
    initSleepForm();
    initAlarmForm();
    initScheduledJobActionForms();
    initDismissibleMessages();
    initPresetSearchAutosubmit();
    initLiveTitleMarquee();
    initNowPlayingRefresh();
    initPlaybackForms();
    initPresetSyncPopover();
    initPresetBoard();
    initStationDrag();
    initPasswordToggles();
    initMillisecondRangeFields();
});
