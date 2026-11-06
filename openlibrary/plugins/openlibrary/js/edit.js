import $ from 'jquery';
import { isbnOverride } from './isbnOverride';
import {
    parseIsbn,
    parseLccn,
    isChecksumValidIsbn10,
    isChecksumValidIsbn13,
    isFormatValidIsbn10,
    isFormatValidIsbn13,
    isValidLccn,
    isIdDupe
} from './idValidation';
import { init as initAutocomplete } from './autocomplete';
import { init as initJqueryRepeat } from './jquery.repeat';
import { trimInputValues } from './utils.js';

// render_* globals are provided by the edit edition, author-autocomplete and
// edit about templates; they are declared for oxlint in .oxlintrc.json.

function error(errordiv, input, message) {
    $(errordiv).show().html(message);
    $(input).trigger('focus');
    return false;
}

function update_len() {
    var len = $('#excerpts-excerpt').val().length;
    var color;
    if (len > 2000) {
        color = '#e44028';
    } else {
        color = 'gray';
    }
    $('#excerpts-excerpt-len')
        .html(2000 - len)
        .css('color', color);
}

/**
 * Gets length of 'textid' section and limit textid value length to input 'limit'
 *
 * @param {String} textid  text section id name
 * @param {Number} limit   character number limit
 * @return {boolean} is character number below or equal to limit
 */
function limitChars(textid, limit) {
    var text = $(`#${textid}`).val();
    var textlength = text.length;
    if (textlength > limit) {
        $(`#${textid}`).val(text.substr(0, limit));
        return false;
    } else {
        return true;
    }
}

/**
 * This is needed because jQuery has no forEach equivalent that works with jQuery elements instead of DOM elements
 * @param selector - css selector used by jQuery
 * @returns {*[]} - array of jQuery elements
 */
function getJqueryElements(selector) {
    const queryResult = $(selector);
    const jQueryElementArray = [];
    for (let i = 0; i < queryResult.length; i++) {
        jQueryElementArray.push(queryResult.eq(i));
    }
    return jQueryElementArray;
}

/**
 * Makes a contributor's "link to author" input autocomplete against author records.
 * The link is optional and independent of the name: the name is what the edition
 * credits, so it is only filled in from the author when blank.
 * @param {JQuery<HTMLElement>} $form  element containing the name and author inputs
 */
function setupContributorAuthorInput($form) {
    const $authorInput = $form.find('.ac-input__visible');
    $authorInput.setup_autocomplete(
        {
            endpoint: '/authors/_autocomplete',
            addnew: false
        },
        {
            minChars: 2,
            max: 11,
            matchSubset: false,
            autoFill: true,
            formatItem: render_lazy_author_preview
        }
    );
    $authorInput.on('autocompleteselect', (_event, ui) => {
        const $name = $form.find('.floating-label__input').not('.ac-input__visible');
        if (!$name.val().trim()) {
            $name.val(ui.item.value);
        }
    });
    // Typing after a selection invalidates it
    $authorInput.on('input', () => $form.find('.ac-input__value').val(''));
}

/**
 * @param {Object} dataConfig  translated messages
 * @param {{role: string, name: string, authorText: string, authorKey: string}} contributor
 * @return {?{message: string, field: string}} the first problem found, if any
 */
function validateContributor(dataConfig, { role, name, authorText, authorKey }) {
    if (role === '' || role === '---') {
        return { message: dataConfig['Please select a role.'], field: 'role' };
    }
    if (name === '') {
        return { message: dataConfig['You need to give this ROLE a name.'].replace(/ROLE/, role), field: 'name' };
    }
    if (authorText && !authorKey) {
        return { message: dataConfig['Select an author from the list, or leave the author field empty.'], field: 'author' };
    }
    return null;
}

/**
 * Sets a contributor row's displayed text and hidden form inputs.
 * @param {JQuery<HTMLElement>} $row
 * @param {{role: string, name: string, authorKey: string}} contributor
 */
function updateContributorRow($row, { role, name, authorKey }) {
    $row.find('.contributor-role').text(role);
    $row.find('.contributor-name').text(name);
    const $roleInput = $row.find('input[name$="--role"]');
    $roleInput.val(role);
    $row.find('input[name$="--name"]').val(name);

    let $keyInput = $row.find('input[name$="--author--key"]');
    if (!$keyInput.length) {
        $keyInput = $('<input type="hidden">').attr('name', $roleInput.attr('name').replace(/--role$/, '--author--key'));
        $roleInput.before($keyInput);
    }
    $keyInput.val(authorKey);

    $row.find('.contributor-author-link').remove();
    if (authorKey) {
        $('<a class="contributor-author-link" target="_blank">')
            .attr('href', authorKey)
            .text(authorKey.split('/').pop())
            .insertAfter($row.find('.contributor-name'));
    }
}

/**
 * Opens an inline editor for a contributor row, built by cloning the add form so the
 * list of roles is only defined once.
 * @param {Object} dataConfig  translated messages
 * @param {JQuery<HTMLElement>} $row
 */
function editContributorRow(dataConfig, $row) {
    if ($row.next().hasClass('contributor-editor')) return;
    const $form = $('#roles-form').clone();
    // The clone must not collide with the add form's ids or submit with the page
    $form.removeAttr('id').find('[id]').removeAttr('id');
    $form.find('[name]').removeAttr('name');
    $form.find('.ac-input__visible').removeClass('ui-autocomplete-input').removeAttr('autocomplete');

    const $role = $form.find('select');
    const $name = $form.find('.floating-label__input').not('.ac-input__visible');
    const $authorText = $form.find('.ac-input__visible');
    const $authorKey = $form.find('.ac-input__value');
    const authorKey = $row.find('input[name$="--author--key"]').val() || '';
    $role.val($row.find('input[name$="--role"]').val());
    $name.val($row.find('input[name$="--name"]').val());
    $authorText.val(authorKey.split('/').pop());
    $authorKey.val(authorKey);

    const $save = $('<button type="button" class="larger">').text(dataConfig['Save']);
    const $cancel = $('<button type="button" class="larger">').text(dataConfig['Cancel']);
    $form.find('.repeat-add').replaceWith($('<div class="roles-form__buttons">').append($save, $cancel));
    $form.addClass('roles-form--edit');

    const $editor = $('<tr class="contributor-editor">').append($('<td colspan="3">').append($form));
    const close = () => {
        $editor.remove();
        $row.show();
    };
    $save.on('click', () => {
        const contributor = {
            role: $role.val(),
            name: $name.val().trim(),
            authorText: $authorText.val().trim(),
            authorKey: $authorKey.val()
        };
        const problem = validateContributor(dataConfig, contributor);
        if (problem) {
            const fields = { role: $role, name: $name, author: $authorText };
            return error('#role-errors', fields[problem.field], problem.message);
        }
        $('#role-errors').hide();
        updateContributorRow($row, contributor);
        close();
    });
    $cancel.on('click', close);

    $row.hide().after($editor);
    setupContributorAuthorInput($form);
    $name.trigger('focus');
}

export function initRoleValidation() {
    initJqueryRepeat();
    initAutocomplete();
    const dataConfig = JSON.parse(document.querySelector('#roles').dataset.config);
    const $authorInput = $('#role-author');
    setupContributorAuthorInput($('#roles-form'));

    $('#roles').repeat({
        vars: { prefix: 'edition--' },
        validate: function (data) {
            const problem = validateContributor(dataConfig, {
                role: data.role,
                name: data.name,
                authorText: $authorInput.val().trim(),
                authorKey: data.author
            });
            if (problem) {
                const fields = { role: '#select-role', name: '#role-name', author: '#role-author' };
                return error('#role-errors', fields[problem.field], problem.message);
            }
            $('#role-errors').hide();
            $('#select-role, #role-name, #role-author, #role-author-key').val('');
            return true;
        }
    });
    $('#roles').on('repeat-add', () => {
        $('#roles-display .contributor-author-link[href=""]').remove();
    });
    $('#roles-display').on('click', '.contributor-edit', function () {
        editContributorRow(dataConfig, $(this).closest('tr'));
    });
}

/**
 * Displays a confirmation box in the error div to confirm the addition of an
 * ISBN with a valid form but which fails the checksum.
 * @param {Object} data  data from the input form, gathered via js/jquery.repeat.js
 * @param {String} isbnConfirmString  a const with the HTML to create the confirmation message/buttons
 */
export function isbnConfirmAdd(data) {
    const isbnConfirmString = `ISBN ${data.value} may be invalid. Add it anyway? <button class="repeat-add" id="yes-add-isbn" type="button">Yes</button>&nbsp;<button id="do-not-add-isbn" type="button">No</button>`;
    // Display the error and option to add the ISBN anyway.
    $('#id-errors').show().html(isbnConfirmString);

    const yesButtonSelector = '#yes-add-isbn';
    const noButtonSelector = '#do-not-add-isbn';
    const onYes = () => {
        $('#id-errors').hide();
    };
    const onNo = () => {
        $('#id-errors').hide();
        isbnOverride.clear();
    };
    $(document).on('click', yesButtonSelector, onYes);
    $(document).on('click', noButtonSelector, onNo);

    // Save the data to isbnOverride so it can be picked up via onAdd in
    // js/jquery.repeat.js when the user confirms adding the invalid ISBN.
    isbnOverride.set(data);
    return false;
}

/**
 * Called by validateIdentifiers(), validates the addition of new
 * ISBN 10 to an edition.
 * @param {Object} data  data from the input form
 * @param {Object} dataConfig  object mapping error messages to their string values
 * @param {String} label  formatted value of the identifier type name (ISBN 10)
 * @returns {boolean}  true if ISBN passes validation, else returns false and displays appropriate error
 */
function validateIsbn10(data, dataConfig, label) {
    data.value = parseIsbn(data.value);

    if (!isFormatValidIsbn10(data.value)) {
        return error('#id-errors', '#id-value', dataConfig['ID must be exactly 10 characters [0-9] or X.'].replace(/ID/, label));
    }
    // Here the ISBN has a valid format, but also has an invalid checksum. Give the user a chance to verify
    // the ISBN, as books sometimes issue with invalid ISBNs and we want to be able to add them.
    // See https://en-academic.com/dic.nsf/enwiki/8948#cite_ref-18 for more.
    else if (isFormatValidIsbn10(data.value) === true && isChecksumValidIsbn10(data.value) === false) {
        isbnConfirmAdd(data);
        return false;
    }
    return true;
}

/**
 * Called by validateIdentifiers(), validates the addition of new
 * ISBN 13 to an edition.
 * @param {Object} data  data from the input form
 * @param {Object} dataConfig  object mapping error messages to their string values
 * @param {String} label  formatted value of the identifier type name (ISBN 13)
 * @returns {boolean}  true if ISBN passes validation, else returns false and displays appropriate error
 */
function validateIsbn13(data, dataConfig, label) {
    data.value = parseIsbn(data.value);

    if (isFormatValidIsbn13(data.value) === false) {
        return error('#id-errors', '#id-value', dataConfig['ID must be exactly 13 digits [0-9]. For example: 978-1-56619-909-4'].replace(/ID/, label));
    }
    // Here the ISBN has a valid format, but also has an invalid checksum. Give the user a chance to verify
    // the ISBN, as books sometimes issue with invalid ISBNs and we want to be able to add them.
    // See https://en-academic.com/dic.nsf/enwiki/8948#cite_ref-18 for more.
    else if (isFormatValidIsbn13(data.value) === true && isChecksumValidIsbn13(data.value) === false) {
        isbnConfirmAdd(data);
        return false;
    }
    return true;
}

/**
 * Called by validateIdentifiers(), validates the addition of new
 * LCCN to an edition.
 * @param {Object} data  data from the input form
 * @param {Object} dataConfig  object mapping error messages to their string values
 * @param {String} label  formatted value of the identifier type name (LCCN)
 * @returns {boolean}  true if LCCN passes validation, else returns false and displays appropriate error
 */
function validateLccn(data, dataConfig, label) {
    data.value = parseLccn(data.value);

    if (isValidLccn(data.value) === false) {
        $('#id-value').val(data.value);
        return error('#id-errors', '#id-value', dataConfig['Invalid ID format'].replace(/ID/, label));
    }
    return true;
}

/**
 * Called by initIdentifierValidation(), along with tests in
 * tests/unit/js/editEditionsPage.test.js, to validate the addition of new
 * identifiers (ISBN, LCCN) to an edition.
 * @param {Object} data  data from the input form
 * @returns {boolean}  true if identifier passes validation
 */
export function validateIdentifiers(data) {
    const dataConfig = JSON.parse(document.querySelector('#identifiers').dataset.config);

    if (data.name === '' || data.name === '---') {
        $('#id-value').val(data.value);
        return error('#id-errors', '#select-id', dataConfig['Please select an identifier.']);
    }
    const label = $('#select-id').find(`option[value='${data.name}']`).html();
    if (data.value === '') {
        return error('#id-errors', '#id-value', dataConfig['You need to give a value to ID.'].replace(/ID/, label));
    }
    if (['ocaid'].includes(data.name) && /\s/g.test(data.value)) {
        return error('#id-errors', '#id-value', dataConfig['ID ids cannot contain whitespace.'].replace(/ID/, label));
    }

    let validId = true;
    if (data.name === 'isbn_10') {
        validId = validateIsbn10(data, dataConfig, label);
    } else if (data.name === 'isbn_13') {
        validId = validateIsbn13(data, dataConfig, label);
    } else if (data.name === 'lccn') {
        validId = validateLccn(data, dataConfig, label);
    }

    // checking for duplicate identifier entry on all identifier types
    // expects parsed ids so placed after validate
    const entries = document.querySelectorAll(`.${data.name}`);
    if (isIdDupe(entries, data.value) === true) {
        // isbnOverride being set will override the dupe checker, so clear isbnOverride if there's a dupe.
        if (isbnOverride.get()) {
            isbnOverride.clear();
        }
        return error('#id-errors', '#id-value', dataConfig['That ID already exists for this edition.'].replace(/ID/, label));
    }

    if (validId === false) return false;
    $('#id-errors').hide();
    return true;
}

export function initClassificationValidation() {
    initJqueryRepeat();
    const dataConfig = JSON.parse(document.querySelector('#classifications').dataset.config);

    // Prevent form submission on Enter for classification fields
    $('#classification-value').on('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            $('#classifications .repeat-add').trigger('click');
            return false;
        }
    });

    $('#classifications').repeat({
        vars: { prefix: 'edition--' },
        validate: function (data) {
            if (data.name === '' || data.name === '---') {
                return error('#classification-errors', '#select-classification', dataConfig['Please select a classification.']);
            }
            if (data.value === '') {
                const label = $('#select-classification').find(`option[value='${data.name}']`).html();
                return error('#classification-errors', '#classification-value', dataConfig['You need to give a value to CLASS.'].replace(/CLASS/, label));
            }
            $('#classification-errors').hide();
            $('#select-classification, #classification-value').val('');
            return true;
        }
    });
}

export function initLanguageMultiInputAutocomplete() {
    initAutocomplete();
    $(function () {
        getJqueryElements('.multi-input-autocomplete--language').forEach(jqueryElement => {
            jqueryElement.setup_multi_input_autocomplete(
                render_language_field,
                {
                    endpoint: '/languages/_autocomplete',
                    sortable: true
                },
                {
                    max: 6,
                    formatItem: render_language_autocomplete_item
                }
            );
        });
    });
}

export function initWorksMultiInputAutocomplete() {
    initAutocomplete();
    $(function () {
        getJqueryElements('.multi-input-autocomplete--works').forEach(jqueryElement => {
            /* Values in the html passed from Python code */
            const dataConfig = JSON.parse(jqueryElement[0].dataset.config || '{}');
            jqueryElement.setup_multi_input_autocomplete(
                render_work_field,
                {
                    endpoint: '/works/_autocomplete',
                    addnew: dataConfig['addnew'] || false,
                    new_name: dataConfig['new_name'] || '',
                    allow_empty: dataConfig['allow_empty'] || false
                },
                {
                    minChars: 2,
                    max: 11,
                    matchSubset: false,
                    autoFill: true,
                    formatItem: render_work_autocomplete_item
                }
            );
        });
    });

    // Show the new work options checkboxes only if "New work" selected
    $('input[name="works--0"]').on('autocompleteselect', function (_event, ui) {
        $('.new-work-options').toggle(ui.item.key === '__new__');
    });
}

export function initSeedsMultiInputAutocomplete() {
    initAutocomplete();
    $(function () {
        getJqueryElements('.multi-input-autocomplete--seeds').forEach(jqueryElement => {
            /* Values in the html passed from Python code */
            jqueryElement.setup_multi_input_autocomplete(
                render_seed_field,
                {
                    endpoint: '/works/_autocomplete',
                    addnew: false,
                    allow_empty: true,
                    sortable: true
                },
                {
                    minChars: 2,
                    max: 11,
                    matchSubset: false,
                    autoFill: true,
                    formatItem: render_lazy_work_preview
                }
            );
        });
    });
}

export function initAuthorMultiInputAutocomplete() {
    initAutocomplete();
    getJqueryElements('.multi-input-autocomplete--author').forEach(jqueryElement => {
        /* Values in the html passed from Python code */
        const dataConfig = JSON.parse(jqueryElement[0].dataset.config);
        jqueryElement.setup_multi_input_autocomplete(
            render_author.bind(null, dataConfig.name_path, dataConfig.dict_path, false),
            {
                endpoint: '/authors/_autocomplete',
                // Don't render "Create new author" if searching by key
                addnew: query => !/OL\d+A/i.test(query),
                sortable: true
            },
            {
                minChars: 2,
                max: 11,
                matchSubset: false,
                autoFill: true,
                formatItem: render_author_autocomplete_item
            }
        );
    });
}

export function initSeriesMultiInputAutocomplete() {
    initAutocomplete();
    getJqueryElements('.multi-input-autocomplete--series').forEach(jqueryElement => {
        /* Values in the html passed from Python code */
        const dataConfig = JSON.parse(jqueryElement[0].dataset.config);
        jqueryElement.setup_multi_input_autocomplete(
            render_series.bind(null, dataConfig.name_path, dataConfig.dict_path, false),
            {
                endpoint: '/series/_autocomplete',
                // Don't render "Create new series" if searching by key
                addnew: query => !/OL\d+L/i.test(query),
                sortable: true
            },
            {
                minChars: 2,
                max: 11,
                matchSubset: false,
                autoFill: true,
                formatItem: render_series_autocomplete_item
            }
        );
    });
}

export function initSubjectsAutocomplete() {
    initAutocomplete();
    getJqueryElements('.csv-autocomplete--subjects').forEach(jqueryElement => {
        const dataConfig = JSON.parse(jqueryElement[0].dataset.config);
        jqueryElement.setup_csv_autocomplete(
            'textarea',
            {
                endpoint: `/subjects_autocomplete?type=${dataConfig.facet}`,
                addnew: false
            },
            {
                formatItem: render_subject_autocomplete_item
            }
        );
    });

    /* Resize textarea to fit on input */
    $('.csv-autocomplete--subjects textarea').on('input', function () {
        this.style.height = 'auto';
        this.style.height = `${this.scrollHeight + 5}px`;
    });
}

export function initEditRow() {
    document.querySelector('#add_row_button').addEventListener('click', () => add_row('website'));
}

/**
 * Adds another input box below the last when adding multiple websites to user profile.
 * @param string name - when prefixed with clone_ should match an element identifier in the page. e.g. if name would refer to clone_website
 */
function add_row(name) {
    const inputBoxes = document.querySelectorAll(`#clone_${name} input`);
    const inputBox = document.createElement('input');
    inputBox.name = `${name}#${inputBoxes.length}`;
    inputBox.type = 'url';
    inputBox.placeholder = 'https://...';
    inputBoxes[inputBoxes.length - 1].after(inputBox);
}

function show_hide_title() {
    if ($('#excerpts-display .repeat-item').length > 1) {
        $('#excerpts-so-far').show();
    } else {
        $('#excerpts-so-far').hide();
    }
}

export function initEditExcerpts() {
    initJqueryRepeat();
    $('#excerpts').repeat({
        vars: {
            prefix: 'work--excerpts'
        },
        validate: function (data) {
            const i18nStrings = JSON.parse(document.querySelector('#excerpts-errors').dataset.i18n);

            if (!data.excerpt) {
                return error('#excerpts-errors', '#excerpts-excerpt', i18nStrings['empty_excerpt']);
            }
            if (data.excerpt.length > 2000) {
                return error('#excerpts-errors', '#excerpts-excerpt', i18nStrings['over_wordcount']);
            }
            $('#excerpts-errors').hide();
            $('#excerpts-excerpt').val('');
            return true;
        }
    });

    // update length on every keystroke
    $('#excerpts-excerpt').on('keyup', function () {
        limitChars('excerpts-excerpt', 2000);
        update_len();
    });

    // update length on add.
    $('#excerpts').on('repeat-add', update_len).on('repeat-add', show_hide_title).on('repeat-remove', show_hide_title);

    // update length on load
    update_len();
    show_hide_title();
}

/**
 * Initializes links element on edit page.
 *
 * Assumes presence of elements with id:
 *    - '#links' and 'data-prefix' attribute
 *    - '#link-label'
 *    - '#link-url'
 *    - '#link-errors'
 */
export function initEditLinks() {
    initJqueryRepeat();
    $('#links').repeat({
        vars: {
            prefix: $('#links').data('prefix')
        },
        validate: function (data) {
            const i18nStrings = JSON.parse(document.querySelector('#link-errors').dataset.i18n);
            const url = data.url.trim();

            if (data.title.trim() === '') {
                $('#link-errors').html(i18nStrings['empty_label']);
                $('#link-errors').removeClass('hidden');
                $('#link-label').trigger('focus');
                return false;
            }
            if (url === '') {
                $('#link-errors').html(i18nStrings['empty_url']);
                $('#link-errors').removeClass('hidden');
                $('#link-url').trigger('focus');
                return false;
            }
            if (!isValidURL(url)) {
                $('#link-errors').html(i18nStrings['invalid_url']);
                $('#link-errors').removeClass('hidden');
                $('#link-url').trigger('focus');
                return false;
            }
            $('#link-errors').addClass('hidden');
            $('#link-label, #link-url').val('');
            return true;
        }
    });
}

/**
 * Initializes edit page.
 *
 * Assumes presence of elements with id:
 *    - '#contentHead'
 *
 * A `#tab/field` hash deep-links into the form: <ol-tabs> selects the tab
 * itself, and this focuses the field with id `tab-field`.
 */
export async function initEdit() {
    trimInputValues('.olform input');

    const [tab, field] = document.location.hash.slice(1).split('/');
    const input = field && document.getElementById(`${tab}-${field}`);
    if (!input) return;
    // The field can only take focus once <ol-tabs> has shown its panel.
    await customElements.whenDefined('ol-tabs');
    input.focus();
    // scroll such that top of the content is visible
    document.getElementById('contentHead')?.scrollIntoView();
}

/**
 * Assesses URL validity using built-in URL object.
 * @param string url
 */
export function isValidURL(url) {
    try {
        new URL(url);
        return true;
    } catch (e) {
        return false;
    }
}

/**
 * @param {NodeList<HTMLElement>} elems
 */
export function initRecordDeletion(elems) {
    for (const elem of elems) {
        elem.addEventListener('click', event => {
            event.preventDefault();
            const form = elem.form;
            const commentInput = document.querySelector('input[name=_comment]');
            form.appendChild(commentInput);
            // pass elem as submitter so its name/value pair is included in the submission
            form.requestSubmit(elem);
        });
    }
}
