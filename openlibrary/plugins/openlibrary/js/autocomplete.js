// jquery plugins to provide author and language autocompletes.

/**
 * Some extra options for when creating an autocomplete input field
 * @typedef {Object} OpenLibraryAutocompleteOptions
 * @property {string} endpoint url to hit for autocomplete results
 * @property {Function} [addnew] (string -> boolean) when (or whether) to display
 * a "Create new record" element in the autocomplete list. It should take the query
 * and return a boolean.
 */

/**
 * Class that manages events for a list of autocompleting inputs
 */
export class AutocompletingInputList {
    /**
     * @param {HTMLElement} container the element that contains the different inputs.
     * @param {string} autocomplete_selector selector to find the input element use for autocomplete.
     * @param {Function} input_renderer ((index, item) -> html_string) render the ith div.input.
     * @param {OpenLibraryAutocompleteOptions} ol_ac_opts
     * @param {Object} ac_opts options given to override defaults of $.autocomplete; see that.
     */
    constructor(container, autocomplete_selector, input_renderer, ol_ac_opts, ac_opts) {
        this.container = $(container);
        this.autocomplete_selector = autocomplete_selector;
        this.input_renderer = input_renderer;
        this.ol_ac_opts = ol_ac_opts;
        this.ac_opts = ac_opts;

        // first let's init any pre-existing inputs
        this.container.find(this.autocomplete_selector).each((i, el) => {
            new SingleAutocompleteInput(el, this.ol_ac_opts, this.ac_opts);
        });

        this.update_visible();

        this.container.on('click', 'a.remove', event => this.remove_input(event.target));
        this.container.on('click', 'a.add', event => {
            event.preventDefault();
            this.add_input();
        });
    }

    /** Updates visibility of the remove/add buttons */
    update_visible() {
        if (this.container.find('div.input').length > 1) {
            this.container.find('a.remove').show();
        } else {
            this.container.find('a.remove').hide();
        }

        this.container.find('a.add:not(:last)').hide();
        this.container.find('a.add:last').show();
    }

    /** Create a new input and append at the end */
    add_input() {
        const next_index = this.container.find('div.input').length;
        const new_input = $(this.input_renderer(next_index, {key:'', name: ''}));
        this.container.append(new_input);
        new SingleAutocompleteInput(
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
}

/**
 * @private
 * Creates a single autocomplete input to be used inside of {@link AutocompletingInputList}
 */
export class SingleAutocompleteInput {
    /**
     * @param {HTMLInputElement} input_el input element that will become autocompleting.
     * @param {OpenLibraryAutocompleteOptions} ol_ac_opts
     * @param {Object} ac_opts options passed to $.autocomplete; see that.
     */
    constructor(input_el, ol_ac_opts, ac_opts) {
        this.input_el = input_el;
        const default_ac_opts = {
            autoFill: true,
            mustMatch: true,
            formatMatch(item) { return item.name; },
            parse(text) {
                const query = $(input_el).val();
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

        $(this.input_el)
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
        $(`#${this.input_el.id}-key`).val(item.key);
        //adding class directly is not working when tab is pressed. setTimeout seems to be working!
        setTimeout(() => $(this.input_el).addClass('accept'), 0);
    }

    /**
     * Place the visible input element in a "rejected" state, and remove the
     * 'true' (hidden) input element's value.
     */
    rejectValue() {
        $(`#${this.input_el.id}-key`).val('');
        $(this.input_el).addClass('reject');
    }

    /** Reset the accept/reject state of the input */
    resetAcceptReject() {
        $(this.input_el).removeClass('accept').removeClass('reject');
    }
}

export default function($) {
    $.fn.setup_multi_input_autocomplete = function() {
        return new AutocompletingInputList(this, ...arguments);
    }
}
