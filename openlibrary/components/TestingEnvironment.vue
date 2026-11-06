<script setup>
import { shallowRef, computed, watch, onBeforeUnmount } from 'vue';
import TestingRow from './TestingEnvironment/TestingRow.vue';
import DeploySection from './TestingEnvironment/DeploySection.vue';
import { DEFAULT_STRINGS, applyDeployBadge, decodeAndParseJSON, faviconEnv } from './TestingEnvironment/utils.js';
import { useToast } from './TestingEnvironment/composables/useToast.js';
import { useTestingStatus } from './TestingEnvironment/composables/useTestingStatus.js';
import { useActions } from './TestingEnvironment/composables/useActions.js';

defineOptions({ name: 'TestingEnvironment' });

const props = defineProps({
    maintainer: {
        type: String,
        default: 'false'
    },
    jenkinsUrl: {
        type: String,
        default: ''
    },
    /** URI-encoded JSON of translated panel strings.
     * @see render_component() in openlibrary/plugins/upstream/utils.py */
    i18n: {
        type: String,
        default: ''
    }
});

// ── i18n (runs at setup time — same timing as the old `created` hook) ─
// Plain object: translated strings are fixed at render time and never mutate,
// so a ref would add unnecessary proxy overhead.
let strings = { ...DEFAULT_STRINGS };
try {
    const parsed = decodeAndParseJSON(props.i18n);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        strings = { ...DEFAULT_STRINGS, ...parsed };
    }
} catch {
    // A malformed translation payload falls back to English.
}

// ── Composables ─────────────────────────────────────────────────────
const { setToast } = useToast();

// busy marks the action queue as processing: pushes and polls pause while
// it drains, and the drain-end response confirms everything queued.
const busy = shallowRef(false);
const { view, payload, now, retry } = useTestingStatus(busy);
const { refreshing, adding, deploying, updating, addInput, recentlyAdded, togglePr, updatePr, removePr, restorePr, reorderPrs, deploy, refresh, addPrs } =
    useActions({
        busy,
        payload,
        setToast,
        strings
    });

// ── Derived state ───────────────────────────────────────────────────
const isMaintainer = computed(() => props.maintainer === 'true');

const visiblePrs = computed(() => {
    // Merged PRs land in the next deploy regardless; the row is noise.
    return ((payload.value && payload.value.prs) || []).filter(pr => pr.merged !== true);
});

// ── Reordering ──────────────────────────────────────────────────────
// The server lists rows in merge order. Mid-drag, the table follows a local
// order instead, so a pushed snapshot can't yank rows out from under the
// pointer; dropping commits it, and anything else (Escape, a drop outside the
// table) leaves the server's order standing.
const draggedPr = shallowRef(null);
const dragOrder = shallowRef(null);

const prs = computed(() => {
    const order = dragOrder.value;
    if (!order) return visiblePrs.value;
    const rank = new Map(order.map((pr, i) => [pr, i]));
    // Rows outside the order (dropped from the set) stay last.
    return [...visiblePrs.value].sort((a, b) => (rank.get(a.pr) ?? Infinity) - (rank.get(b.pr) ?? Infinity));
});

function inSetOrder() {
    return visiblePrs.value.filter(pr => pr.in_set !== false).map(pr => pr.pr);
}

function onDragStart(pr) {
    draggedPr.value = pr.pr;
    dragOrder.value = inSetOrder();
}

function onDragOver(target, event) {
    const dragged = draggedPr.value;
    if (dragged === null || target.in_set === false) return;
    // Accepting the dragover is what makes the row a drop target.
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    if (target.pr === dragged) return;
    const order = dragOrder.value.filter(pr => pr !== dragged);
    const from = dragOrder.value.indexOf(dragged);
    const to = dragOrder.value.indexOf(target.pr);
    const rect = event.currentTarget.getBoundingClientRect();
    const pastMiddle = event.clientY > rect.top + rect.height / 2;
    // Swap only once the pointer crosses the target's middle, in the
    // direction of travel, so the rows don't flip back and forth.
    if (from < to ? !pastMiddle : pastMiddle) return;
    order.splice(order.indexOf(target.pr) + (from < to ? 1 : 0), 0, dragged);
    dragOrder.value = order;
}

function onDrop(event) {
    if (draggedPr.value === null) return;
    event.preventDefault();
    const order = dragOrder.value;
    if (order.join() !== inSetOrder().join()) reorderPrs(order);
    onDragEnd();
}

function onDragEnd() {
    draggedPr.value = null;
    dragOrder.value = null;
}

function moveBy(pr, delta) {
    const order = inSetOrder();
    const from = order.indexOf(pr.pr);
    const to = from + delta;
    if (from === -1 || to < 0 || to >= order.length) return;
    order.splice(from, 1);
    order.splice(to, 0, pr.pr);
    reorderPrs(order);
}

// ── Deploy favicon badge ────────────────────────────────────────────
let deployBadge = null;

function syncDeployFavicon(isDeploying) {
    if (isDeploying) {
        if (deployBadge) return;
        const links = Array.from(document.querySelectorAll('link[rel="icon"]')).filter(link => faviconEnv(link.getAttribute('href')));
        if (!links.length) return;
        deployBadge = applyDeployBadge(links);
    } else if (deployBadge) {
        deployBadge();
        deployBadge = null;
    }
}

watch(
    () => payload.value?.deploying,
    deploying => syncDeployFavicon(deploying),
    { immediate: true }
);

onBeforeUnmount(() => syncDeployFavicon(false));
</script>

<template>
    <section class="testing-env" :class="{ 'testing-env--deploying': payload?.deploying }" :aria-busy="busy ? 'true' : 'false'">
        <div v-if="view === 'loading'" class="testing-env__main">
            <p class="testing-env__blank" role="status" aria-live="polite">
                {{ strings.loading }}
            </p>
        </div>

        <div v-else-if="view === 'error'" class="testing-env__main">
            <div class="testing-env__blank" role="alert">
                <p>{{ strings.loadError }}</p>
                <button type="button" class="testing-env__btn testing-env__btn--small" @click="retry">
                    {{ strings.retry }}
                </button>
            </div>
        </div>

        <div v-else>
            <div class="testing-env__main">
                <header class="testing-env__bar">
                    <h2 class="testing-env__title">
                        {{ strings.title }}
                    </h2>
                </header>

                <div v-if="prs.length" class="testing-env__table-wrap">
                    <table class="testing-env__table">
                        <thead>
                            <tr>
                                <th v-if="isMaintainer" scope="col" class="testing-env__col-handle">
                                    <span class="sr-only">{{ strings.order }}</span>
                                </th>
                                <th v-if="isMaintainer" scope="col">
                                    {{ strings.next }}
                                </th>
                                <th scope="col">
                                    {{ strings.pr }}
                                </th>
                                <th scope="col">
                                    {{ strings.author }}
                                </th>
                                <th scope="col">
                                    {{ strings.assignee }}
                                </th>
                                <th scope="col">
                                    {{ strings.drift }}
                                </th>
                                <th v-if="isMaintainer" scope="col" class="testing-env__col-actions">
                                    <span class="sr-only">{{ strings.actions }}</span>
                                </th>
                            </tr>
                        </thead>
                        <tbody>
                            <TestingRow
                                v-for="pr in prs"
                                :key="pr.pr"
                                :pr="pr"
                                :maintainer="isMaintainer"
                                :strings="strings"
                                :recent="recentlyAdded.has(pr.pr)"
                                :updating="updating.has(pr.pr)"
                                :dragging="draggedPr === pr.pr"
                                @toggle="togglePr"
                                @update="updatePr"
                                @remove="removePr"
                                @restore="restorePr"
                                @drag-start="onDragStart"
                                @drag-end="onDragEnd"
                                @move="moveBy"
                                @dragover="onDragOver(pr, $event)"
                                @drop="onDrop"
                            />
                        </tbody>
                    </table>
                </div>
                <p v-else class="testing-env__blank">
                    {{ strings.noPrs }}
                </p>

                <form v-if="isMaintainer" method="post" class="testing-env__add" data-add-form @submit.prevent="addPrs">
                    <input
                        id="testing-env-add"
                        v-model="addInput"
                        type="text"
                        name="pr"
                        class="testing-env__input"
                        autocomplete="off"
                        :placeholder="strings.addPlaceholder"
                        :aria-label="strings.addPrs"
                    />
                    <button type="submit" class="testing-env__btn" :disabled="adding">
                        <span v-if="adding" class="testing-env__btn-icon testing-env__spinner" aria-hidden="true" />
                        <svg
                            v-else
                            class="testing-env__btn-icon"
                            width="16"
                            height="16"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            stroke-width="2"
                            stroke-linecap="round"
                            stroke-linejoin="round"
                            aria-hidden="true"
                        >
                            <line x1="12" y1="5" x2="12" y2="19" />
                            <line x1="5" y1="12" x2="19" y2="12" />
                        </svg>
                        {{ strings.add }}
                    </button>
                </form>
            </div>

            <DeploySection
                :payload="payload"
                :now="now"
                :maintainer="isMaintainer"
                :strings="strings"
                :jenkins-url="jenkinsUrl"
                :refreshing="refreshing"
                :deploying="deploying"
                @deploy="deploy"
                @refresh="refresh"
            />
        </div>
    </section>
</template>

<!-- Shadow-DOM styles: rules here style the whole panel; inherited design
     tokens (--color-*, --spacing-*) cross the shadow boundary. The rules live
     in styles.css; <style src> inlines them into this custom element's shadow
     root at build time, so the encapsulation choice is unchanged. -->
<style src="./TestingEnvironment/styles.css"></style>
