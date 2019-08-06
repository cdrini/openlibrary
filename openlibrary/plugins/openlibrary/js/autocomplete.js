// jquery plugins to provide author and language autocompletes.

/**
 * Some extra options for when creating an autocomplete input field
 * @typedef {Object} OpenLibraryAutocompleteOptions
 * @property{string} endpoint - url to hit for autocomplete results
 * @property{Function} [addnew] - (string -> boolean) when (or whether) to display
 * a "Create new record" element in the autocomplete list. It should take the query
 * and return a boolean.
 */

/**
 * @private
 */
export class MultiAutocompleteInput {
    /**
     * @param{HTMLInputElement} input_el - input element that will become autocompleting.
     * @param{OpenLibraryAutocompleteOptions} ol_ac_opts
     * @param{Object} ac_opts - options passed to $.autocomplete; see that.
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
     * @param {Object} item
     */
    acceptValue(item) {
        $(`#${this.input_el.id}-key`).val(item.key);
        //adding class directly is not working when tab is pressed. setTimeout seems to be working!
        setTimeout(() => $(this.input_el).addClass('accept'), 0);
    }

    rejectValue() {
        $(`#${this.input_el.id}-key`).val('');
        $(this.input_el).addClass('reject');
    }

    resetAcceptReject() {
        $(this.input_el).removeClass('accept').removeClass('reject');
    }
}

export default function($) {
    /**
     * @this HTMLElement - the element that contains the different inputs.
     * @param {string} autocomplete_selector - selector to find the input element use for autocomplete.
     * @param {Function} input_renderer - ((index, item) -> html_string) render the ith div.input.
     * @param {OpenLibraryAutocompleteOptions} ol_ac_opts
     * @param {Object} ac_opts - options given to override defaults of $.autocomplete; see that.
     */
    $.fn.setup_multi_input_autocomplete = function(autocomplete_selector, input_renderer, ol_ac_opts, ac_opts) {
        const container = $(this);

        // first let's init any pre-existing inputs
        container.find(autocomplete_selector).each(function() {
            new MultiAutocompleteInput(this, ol_ac_opts, ac_opts);
        });

        function update_visible() {
            if (container.find('div.input').length > 1) {
                container.find('a.remove').show();
            } else {
                container.find('a.remove').hide();
            }

            container.find('a.add:not(:last)').hide();
            container.find('a.add:last').show();
        }

        function add_input() {
            const next_index = container.find('div.input').length;
            const new_input = $(input_renderer(next_index, {key:'', name: ''}));
            container.append(new_input);
            new MultiAutocompleteInput(
                new_input.find(autocomplete_selector)[0],
                ol_ac_opts,
                ac_opts);
            update_visible();
        }

        function remove_input(remove_el) {
            $(remove_el).closest('div.input').remove();
            update_visible();
        }

        update_visible();

        container.on('click', 'a.remove', event => remove_input(event.target));
        container.on('click', 'a.add', event => {
            event.preventDefault();
            add_input();
        });
    };
}
