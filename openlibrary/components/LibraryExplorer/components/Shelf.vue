<template>
  <div
    class="shelf"
    :data-short="node.short"
  >
    <component
      :is="features.shelfLabel === 'slider' ? 'ClassSlider' : 'ShelfLabel'"
      :key="node.short"
      class="shelf-label"
      :node="node"
    >
      <template #extra-actions>
        <button
          v-if="features.shelfLabel === 'slider' && node.children"
          :title="`See a list of the subsections of ${node.short}: ${node.name}`"
          :class="{selected: showShelfIndex}"
          @click="showShelfIndex = !showShelfIndex"
        >
          <IndexIcon />
        </button>
        <button
          v-if="node.children && node.children.length"
          :title="`See more books in ${node.short}: ${node.name}`"
          @click="expandBookshelf(parent, node)"
        >
          <ExpandIcon />
        </button>
      </template>
    </component>

    <ShelfIndex
      v-if="showShelfIndex"
      class="shelf-index"
      :node="node"
    />

    <OLCarousel
      class="shelf-carousel"
      :data-short="
        node.children && node.position != 'root'
          ? node.children[node.position].short
          : node.short
      "
      :query="`${sort.includes('_sort') ? classification.field + '_sort' : classification.field}:${
        node.children && node.position != 'root'
          ? node.children[node.position].query
          : node.query
      } ${filter}`"
      :node="
        node.children && node.position != 'root'
          ? node.children[node.position]
          : node
      "
      :sort="sort"
      :fetch-coordinator="fetchCoordinator"
    >
      <template #book-end-start>
        <div class="book-end-start">
          <h3>
            {{
              node.children && node.position != "root"
                ? node.children[node.position].name
                : node.name
            }}
          </h3>
        </div>
      </template>

      <template #cover="{ book }">
        <BookCover3D
          v-if="features.book3d"
          :width="150"
          :height="200"
          :thickness="50"
          :book="book"
          :cover="features.cover"
        />
        <FlatBookCover
          v-else
          :book="book"
          :cover="features.cover"
        />
      </template>

      <template #cover-label="{ book }">
        <div
          v-if="book[classification.field] && labels.includes('classification')"
          :title="
            book[classification.field]
              .map(classification.fieldTransform)
              .join('\n')
          "
        >
          {{
            classification.fieldTransform(classification.chooseBest(book[classification.field]))
          }}
        </div>
        <div v-if="labels.includes('first_publish_year')">
          {{ book.first_publish_year }}
        </div>
        <div v-if="labels.includes('edition_count')">
          {{ book.edition_count }} editions
        </div>
      </template>
    </OLCarousel>
  </div>
</template>

<script>
import OLCarousel from './OLCarousel.vue';
import ClassSlider from './ClassSlider.vue';
import ShelfLabel from './ShelfLabel.vue';
import BookCover3D from './BookCover3D.vue';
import FlatBookCover from './FlatBookCover.vue';
import ShelfIndex from './ShelfIndex.vue';
import ExpandIcon from './icons/ExpandIcon.vue';
import IndexIcon from './icons/IndexIcon.vue';
import maxBy from 'lodash/maxBy';

class FetchCoordinator {
    constructor() {
        this.requestedFetches = [];
        /** @type { 'idle' | 'active' } */
        this.state = 'idle';

        this.runningRequests = 0;

        this.timeout = null;
        this.maxConcurrent = 6;
        this.groupingTime = 250;
    }

    async fetch({ priority, name }, ...args) {
        return new Promise((resolve, reject) => {
            this.enqueue({
                priority,
                name,
                args,
                resolve,
                reject,
            });
        });
    }

    enqueue(fetchRequest) {
        // console.log(`Enqueuing request #${this.requestedFetches.length + 1}: ${fetchRequest.name}`);
        this.requestedFetches.push(fetchRequest);
        this.activate();
    }

    activate() {
        if (this.requestedFetches.length && !this.timeout) {
            this.state = 'active'
            this.timeout = setTimeout(() => this.consume(), this.groupingTime);
        } else {
            this.state = 'idle';
        }
    }

    consume() {
        this.timeout = null;
        while ((this.maxConcurrent - this.runningRequests > 0) && this.requestedFetches.length) {
            const topRequest = maxBy(this.requestedFetches, f => f.priority());
            // console.log(`Completing request w p=${topRequest.priority()}: ${topRequest.name}`)
            this.runningRequests++;
            fetch(...topRequest.args)
                .then(r => {
                    this.runningRequests--;
                    topRequest.resolve(r);
                })
                .catch(e => {
                    this.runningRequests--;
                    topRequest.reject(e);
                });
            const indexToRemove = this.requestedFetches.indexOf(topRequest);
            this.requestedFetches.splice(indexToRemove, 1);
        }
        this.activate();
    }
}

const fetchCoordinator = new FetchCoordinator();

export default {
    components: {
        OLCarousel,
        ClassSlider,
        BookCover3D,
        FlatBookCover,
        ShelfIndex,
        ShelfLabel,
        ExpandIcon,
        IndexIcon,
    },
    props: {
        /** @type {import('../utils').ClassificationNode} */
        node: Object,
        parent: Object,

        labels: Array,
        /** @type {import('../utils').ClassificationTree} */
        classification: Object,
        expandBookshelf: Function,
        features: Object,
        filter: String,
        sort: String,
    },

    data() {
        return {
            showShelfIndex: false,
            fetchCoordinator: fetchCoordinator,
        };
    }
};
</script>

<style scoped>
.shelf-carousel {
  border: 3px solid black;
  margin-top: 10px;
  border-radius: 4px;
  height: 285px;
  background: #EEE;
  contain: strict;
}

.shelf >>> .book {
  justify-content: flex-end;
  margin-bottom: 10px;
}

.shelf >>> .book:first-child .book-3d,
.shelf >>> .book-end-start + .book .book-3d {
  margin-left: 20px;
}

.shelf-label {
  border-radius: 0;
  background: black;
  color: white;
}

button {
  border: 0;
  background: 0;
  padding: 6px 8px;
  font: inherit;
  color: inherit;
}
</style>
