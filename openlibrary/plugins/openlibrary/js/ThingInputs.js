/**
 * Provides auto-completing inputs for OL things (works, languages, etc).
 */

/**
 * Some extra options for when creating an autocomplete input field
 * @typedef {Object} OpenLibraryAutocompleteOptions
 * @property {string} endpoint url to hit for autocomplete results
 * @property {Function} [addnew] (string -> boolean) when (or whether) to display
 * a "Create new record" element in the autocomplete list. It should take the query
 * and return a boolean.
 */

/**
 * Class that manages UI events for a list of inputs that let you select Open Library
 * "Things" (works, authors, etc).
 */
export class ThingInputList {
    /**
     * @param {HTMLElement} container the element that contains the different inputs.
     * @param {string} autocomplete_selector selector to find the visible input element to use
     * for autocomplete inside a `div.input`.
     * @param {Function} input_renderer ((index, item) -> html_string) render the ith `div.input`
     * The renderer is responsible for ensuring that this contains 2 `<input>`s are
     * created (see {@link ThingInput}).
     * @param {OpenLibraryAutocompleteOptions} ol_ac_opts
     * @param {Object} ac_opts options given to override defaults of $.autocomplete; see that.
     */
    constructor(container, autocomplete_selector, input_renderer, ol_ac_opts, ac_opts) {
        this.$container = $(container);
        this.autocomplete_selector = autocomplete_selector;
        this.input_renderer = input_renderer;
        this.ol_ac_opts = ol_ac_opts;
        this.ac_opts = ac_opts;

        // first let's init any pre-existing inputs
        this.$container.find(this.autocomplete_selector).each((i, el) => {
            new ThingInput(el, this.ol_ac_opts, this.ac_opts);
        });

        this.update_visible();

        this.$container.on('click', 'a.remove', event => this.remove_input(event.target));
        this.$container.on('click', 'a.add', event => {
            event.preventDefault();
            this.add_input();
        });
    }

    /** Updates visibility of the remove/add buttons */
    update_visible() {
        if (this.$container.find('div.input').length > 1) {
            this.$container.find('a.remove').show();
        } else {
            this.$container.find('a.remove').hide();
        }

        this.$container.find('a.add:not(:last)').hide();
        this.$container.find('a.add:last').show();
    }

    /** Create a new input and append at the end */
    add_input() {
        const next_index = this.$container.find('div.input').length;
        const new_input = $(this.input_renderer(next_index, {key:'', name: ''}));
        this.$container.append(new_input);
        new ThingInput(
            new_input.find(this.autocomplete_selector)[0],
            this.ol_ac_opts,
            this.ac_opts);
        this.update_visible();
    }

    /**
     * Remove the input item which contains this "remove" button
     * @param {HTMLElement} remove_el
     */
    remove_input(remove_el) {
        $(remove_el).closest('div.input').remove();
        this.update_visible();
    }

    /**
     * Extend jquery to include this class as a method
     * @param {JQuery} $
     */
    static extend_jquery($) {
        $.fn.setup_multi_input_autocomplete = function() {
            return new ThingInputList(this, ...arguments);
        };
    }
}

/**
 * @private
 * Creates a single autocompleting input to be used inside of {@link ThingInputList}
 */
export class ThingInput {
    /**
     * Expects there to exist a hidden input element with id `{{input_el.id}}-key`; this
     * is where the key is stored.
     * @param {HTMLInputElement} visible_input input element that will become autocompleting.
     * @param {OpenLibraryAutocompleteOptions} ol_ac_opts
     * @param {Object} ac_opts options passed to $.autocomplete; see that.
     */
    constructor(visible_input, ol_ac_opts, ac_opts) {
        this.$visible_input = $(visible_input);
        this.$hidden_input = $(`#${visible_input.id}-key`);
        const default_ac_opts = {
            autoFill: true,
            mustMatch: true,
            formatMatch(item) { return item.name; },
            parse(text) {
                const query = $(visible_input).val();
                const rows = typeof text === 'string' ? JSON.parse(text) : text;
                let parsed = rows.map(row => { return {
                    data: row,
                    value: row.name,
                    result: row.name
                }});

                if (ol_ac_opts.addnew && ol_ac_opts.addnew(query)) {
                    parsed = parsed.slice(0, ac_opts.max - 1);
                    parsed.push({
                        data: {name: query, key: '__new__'},
                        value: query,
                        result: query
                    });
                }
                return parsed;
            },
        };

        this.$visible_input
            .autocomplete(ol_ac_opts.endpoint, $.extend(default_ac_opts, ac_opts))
            .result((_, item) => this.acceptValue(item))
            .nomatch(this.rejectValue.bind(this))
            .keypress(this.resetAcceptReject.bind(this));
    }

    /**
     * Place the visible input element in an "accepted" state, and update the
     * 'true' (hidden) input element's value.
     * @param {Object} item
     */
    acceptValue(item) {
        this.$hidden_input.val(item.key);
        //adding class directly is not working when tab is pressed. setTimeout seems to be working!
        setTimeout(() => $(this.$visible_input).addClass('accept'), 0);
    }

    /**
     * Place the visible input element in a "rejected" state, and remove the
     * 'true' (hidden) input element's value.
     */
    rejectValue() {
        this.$hidden_input.val('');
        this.$visible_input.addClass('reject');
    }

    /** Reset the accept/reject state of the input */
    resetAcceptReject() {
        this.$visible_input.removeClass('accept').removeClass('reject');
    }
}
