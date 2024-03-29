<template>
  <div id="app">
    <BookRoom
      :classification="libraryState.settings.selectedClassification"
      :filter="computedFilter"
      :sort="libraryState.sortState.order"
      :class="bookRoomClass"
      :features="bookRoomFeatures"
      :appSettings="libraryState.settings"
      :jumpTo="libraryState.jumpTo"
    />

    <LibraryToolbar :libraryState="libraryState" />
  </div>
</template>

<script>
import BookRoom from './LibraryExplorer/components/BookRoom';
import LibraryToolbar from './LibraryExplorer/components/LibraryToolbar';
import DDC from './LibraryExplorer/ddc.json';
import LCC from './LibraryExplorer/lcc.json';
import { recurForEach } from './LibraryExplorer/utils.js';
import { sortable_lcc_to_short_lcc, short_lcc_to_sortable_lcc } from './LibraryExplorer/utils/lcc.js';
import maxBy from 'lodash/maxBy';

class FilterState {
    constructor() {
        this.filter = '';
        /** @type { '' | 'true' | 'false' } */
        this.has_ebook = 'true';
        /** @type {Array<{name: string, key: string}>} */
        this.languages = [];
        this.age = '';
        this.year = '[1985 TO 9998]';
    }

    solrQueryParts() {
        const filters = this.filter ? [this.filter] : [];
        if (this.has_ebook) {
            filters.push(`has_fulltext:${this.has_ebook}`);
        }

        if (this.languages.length) {
            const langs = this.languages.map(lang => lang.key.split('/')[2]);
            filters.push(`language:(${langs.join(' OR ')})`);
        }
        if (this.age) {
            filters.push(`subject:${this.age}`);
        }
        if (this.year) {
            filters.push(`first_publish_year:${this.year}`);
        }
        return filters;
    }

    solrQuery() {
        return this.solrQueryParts().join(' AND ');
    }
}

/** @type {import('./LibraryExplorer/utils').ClassificationTree[]} */
const CLASSIFICATIONS = [
    {
        name: 'DDC',
        longName: 'Dewey Decimal Classification',
        field: 'ddc',
        fieldTransform: ddc => ddc,
        toQueryFormat: ddc => ddc,
        chooseBest: ddcs => maxBy(ddcs, ddc => ddc.replace(/[\d.]/g, '') ? ddc.length : 100 + ddc.length),
        root: recurForEach({ children: DDC, query: '*' }, n => {
            n.position = 'root';
            n.offset = 0;
            n.requests = {};
        })
    },
    {
        name: 'LCC',
        longName: 'Library of Congress Classification',
        field: 'lcc',
        fieldTransform: sortable_lcc_to_short_lcc,
        toQueryFormat: lcc => {
            const normalized = short_lcc_to_sortable_lcc(lcc);
            return normalized ? normalized.split(' ')[0] : lcc;
        },
        chooseBest: lccs => maxBy(lccs, lcc => lcc.length),
        root: recurForEach({ children: LCC, query: '*' }, n => {
            n.position = 'root';
            n.offset = 0;
            n.requests = {};
        })
    }
];

class LibraryState {
    constructor() {
        this.filterState = new FilterState();
        this.sortState = {
            order: `random_${new Date().toISOString().split(':')[0]}`,
        };
        this.jumpTo = null;
        this.settings = {
            selectedClassification: CLASSIFICATIONS[0],
            classifications: CLASSIFICATIONS,
            labels: ['classification'],
            styles: {
                book: {
                    options: [
                        'default',
                        '3d',
                        'spines',
                        '3d-spines',
                        '3d-flat'
                    ],
                    selected: 'default'
                },

                cover: {
                    options: [
                        'image',
                        'text'
                    ],
                    selected: 'image'
                },

                shelfLabel: {
                    debugModeOnly: true,
                    options: ['slider', 'expander'],
                    selected: 'slider'
                },

                aesthetic: {
                    debugModeOnly: true,
                    options: ['mockup', 'wip'],
                    selected: 'wip'
                },

                scrollbar: {
                    options: ['default', 'thin', 'hidden'],
                    selected: 'thin',
                },
            },
        };
    }

    findClassification(field) {
        return this.settings.classifications.find(c => c.field === field);
    }

    static fromUrl(location=window.location) {
        const urlParams = new URLSearchParams(location.search);
        const state = new LibraryState();
        state.sortState.order = urlParams.get('sort') || state.sortState.order;

        // /explore/ddc (i.e. the second part of th path) should load the DDC classification, etc.
        const classificationName = location.pathname.split('/')[2];
        if (classificationName) {
            state.settings.selectedClassification = state.findClassification(classificationName) || state.settings.selectedClassification;
        }

        if (urlParams.has('jumpTo')) {
            const [classificationName, classificationString] = urlParams.get('jumpTo').split(':');
            state.settings.selectedClassification = state.findClassification(classificationName);
            state.jumpTo = state.settings.selectedClassification.toQueryFormat(classificationString);
        }
        return state;
    }

    toUrl() {
        // Update the path to be /explore/{classification.field}
        let path = `/explore/${this.settings.selectedClassification.field}`;
        if (this.settings.selectedClassification.field === 'ddc') {
            path = '/explore';
        }
        const url = new URL(window.location);
        url.pathname = path;

        const params = {
            sort: this.sortState.order,
            jumpTo: this.jumpTo ? `${this.settings.selectedClassification.field}:${this.jumpTo}` : undefined
        };
        // Don't add to url if the value is default
        if (params.sort.startsWith('random_')) {
            delete params.sort;
        }
        if (!params.jumpTo) {
            delete params.jumpTo;
        }
        url.search = new URLSearchParams(params).toString();

        // Update the url without causing a refresh and without adding a new entry to the history
        window.history.replaceState({}, '', url.toString());
    }
}

export default {
    components: {
        BookRoom,
        LibraryToolbar,
    },
    data() {
        return {
            libraryState: LibraryState.fromUrl(),
        };
    },

    computed: {
        computedFilter() {
            return this.libraryState.filterState.solrQuery();
        },

        bookRoomFeatures() {
            return {
                book3d: this.libraryState.settings.styles.book.selected.startsWith('3d'),
                cover: this.libraryState.settings.styles.cover.selected,
                shelfLabel: this.libraryState.settings.styles.shelfLabel.selected
            };
        },

        bookRoomClass() {
            return Object.entries(this.libraryState.settings.styles)
                .map(([key, val]) => `style--${key}--${val.selected}`)
                .join(' ');
        }
    }
};
</script>

<style lang="less">
#app {
  font-family: "Avenir", Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: rgba(0, 0, 0, .7);
}

details[open] summary ~ * {
  animation: sweep .2s;
}

@keyframes sweep {
  0% {
    opacity: 0;
  }
  100% {
    opacity: 1;
  }
}

.class-slider {
  margin-bottom: 5px;
}

hr {
  width: 100%;
}

.book-room {
  .class-slider .sections {
    background: rgba(255, 255, 255, .3);
    --highlight-color: rgba(255, 255, 255, .5);
    border-radius: 4px;
    height: 4px;
  }

  .book-end-start {
    display: none;
  }

  .book {
    position: relative;
    margin-left: 10px;
  }
  .book-end-wrapper + .book { margin-left: 20px;}

  .cover-label {
    background: rgba(0, 0, 0, .5);
    position: absolute;
    bottom: 0;
    font-size: .8em;
    opacity: .9;
    line-height: .8em;
  }
}

.book-room.style--book--spines {
  .book {
    animation: 200ms slide-in;
    transition: width .2s;
    transition-delay: .5s;
    width: 40px;
    margin: 0;
    overflow: hidden;
    overflow: clip;
    flex-shrink: 0;
    margin-left: 1px;
  }

  .book img {
    width: 150px;
    margin-left: -40px;
    object-fit: cover;
    object-position: center;
    transition: margin-left .2s;
    transition-delay: .5s;
  }
  .book:hover img {
    margin-left: 0;
  }
  .book:hover {
    width: 150px;
  }
}

.book-room.style--book--3d,
.book-room.style--book--3d-spines,
.book-room.style--book--3d-flat {
  .cover {
    opacity: .8;
    transition: opacity .2s;
  }
  .book-end-wrapper + .book { margin-left: 60px; }
  .book:hover .cover {
    opacity: 1;
  }
  .book:hover .book-3d.css-box {
    backface-visibility: hidden;
    transform: perspective(2000px) translate3d(-40px, 0, 100px) rotateY(-15deg);
  }

  .css-box,
  .css-box > * {
    transition-duration: .2s;
    transition-property: width, height, transform;
  }
}

.book-room.style--book--3d-spines {
  .book {
    margin-left: -100px;
  }

  .book:hover {
    z-index: 1;
  }
}

.book-room.style--book--3d-flat {
  .css-box {
    transform: unset !important;
  }
  .book {
    transform: rotateX(20deg);
    transform-style: preserve-3d;
    margin-left: 18px;
  }
  .book-end-wrapper + .book { margin-left: 40px; }
  .books-carousel {
    perspective: 2000px;
  }
}

#app {
  font-family: "bahnschrift", -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji",
    "Segoe UI Symbol";
}

.book-room.style--aesthetic--wip {
  background: linear-gradient(180deg,#ebdfc5 100px, #dbbe9f 1600px,#cba37e 4800px);
  background-position: scroll;

  .classification-short {
    opacity: .6;
  }
  .bookshelf-wrapper {
    margin-left: 140px;
  }
  .bookshelf {
    // background: linear-gradient(
    //   to bottom,
    //   #543721 0px,
    //   #5a3b23 10px,
    //   #7b5130 10px,
    //   #5f432d
    // );
    background: linear-gradient(
      to right,
      rgba(29, 5, 0, .5),
      rgba(61, 0, 0, .1) 20%,
      transparent,
      rgba(61, 0, 0, .1) 60%,
      rgba(29, 5, 0, .5)
    ),
      linear-gradient(
      to bottom,
      transparent 0,
      transparent 10px,
      rgba(255, 191, 0, .4) 10px,
      rgba(255, 191, 0, .1) 15px,
      transparent 50px
    ),
      linear-gradient(
      to bottom,
      #543721 0,
      #5a3b23 10px,
      #7b5130 10px,
      #5f432d
    );
    border: 0;
    padding: 10px;
    padding-top: 36px;
    box-sizing: border-box;
    color: white;
  }

  .bookshelf-name button {
    background: none;
    border: none;
    color: inherit;

    padding: 6px 8px;

    transition: background-color 0.2s;
    cursor: pointer;
  }

  .bookshelf-name button:hover {
    background: rgba(255, 255, 255, .1);
  }

  .shelf {
    margin-bottom: 35px;

    .shelf-index {
      padding: 4px 8px;
      opacity: 0.95;

      a {
        border-radius: 4px;
        border: 1px solid transparent;
        line-height: 0.9em;
      }
      .selected {
        background: rgba(255,255,255, 0.1);
        border: 1px solid white;
        border-left-width: 4px;
        color: inherit;
      }

      .shelf-label--subclasses--count {
        font-size: 0.8em;
        font-weight: 300;
        opacity: 0.8;
        &::before {
          content: "• "
        }
      }
    }
  }

  .shelf-carousel {
    border: 0;
    margin: 0 10px;
    @media (max-width: 450px) { margin: 0; }
    background-color: #563822;
    background-image: linear-gradient(
      to bottom,
      rgba(0, 0, 0, .36),
      #563822 50px
    );
  }

  .class-slider.shelf-label {
    border: 0;
    background: none;
    color: rgba(255, 255, 255, .9);
    font-weight: 400;
    margin: 0;
    min-height: 2em;
  }

  .shelf-label {
    background: none;
  }

  .shelf-label button {
    border: 0;
    border-radius: 4px;
    transition: background-color .2s;
    cursor: pointer;

    &.selected {
      border: 1px solid white;
      background-color: rgba(255, 255, 255, .3);
    }
  }

  .shelf-label button:hover {
    background-color: rgba(255, 255, 255, .2);
  }
  .shelf-label .sections {
    height: 4px;
    bottom: 0;
  }
}

.book-room.style--scrollbar--thin {
  .books-carousel { scrollbar-width: thin; }

  // Chrome-specific scroll fixes
  .books-carousel::-webkit-scrollbar { height: 6px; }
  .books-carousel::-webkit-scrollbar-thumb { background: rgba(255,255,255, 0.35); }
  .books-carousel::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255, 0.25); }
  .books-carousel::-webkit-scrollbar-track { background: rgba(0, 0, 0, 0.2); }
}

.book-room.style--scrollbar--hidden {
  .books-carousel { scrollbar-width: none; }
  .books-carousel::-webkit-scrollbar { height: 0; }
}
</style>
