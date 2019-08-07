import jquery from 'jquery';
import sinon from 'sinon';
import { ThingInputList, ThingInput } from '../../../openlibrary/plugins/openlibrary/js/autocomplete';

let sandbox;

beforeEach(() => {
    sandbox = sinon.createSandbox();
    global.$ = jquery;
    sandbox.stub(global, '$').callsFake(jquery);
    // FIXME make this importable
    const autocompleteStub = {};
    autocompleteStub.result = sinon.stub().returns(autocompleteStub);
    autocompleteStub.nomatch = sinon.stub().returns(autocompleteStub);
    autocompleteStub.keypress = sinon.stub().returns(autocompleteStub);
    global.$.fn.autocomplete = sinon.stub().returns(autocompleteStub);
});

describe('ThingInputList', () => {
    test('Can extend the jquery object', () => {
        expect('setup_multi_input_autocomplete' in $.fn).toBe(false);
        ThingInputList.extend_jquery($);
        expect('setup_multi_input_autocomplete' in $.fn).toBe(true);
    });

    test('Does not error when initialized with no elements', () => {
        new ThingInputList(
            $('<div/>')[0],
            '.foo',
            () => '<div class="input" />',
            {},
            {}
        );
    });

    test('add_input creates children', () => {
        const $container = $('<div/>');
        const list = new ThingInputList(
            $container[0],
            '.foo',
            i => `<div class="input"><input class="foo" id="foo--${i}"></div>`,
            {},
            {}
        );
        for (let i = 1; i <= 5; i++) {
            list.add_input();
            expect($container.find('div.input').length).toBe(i);
        }
    });

    test('add_input calls renderer with correct index', () => {
        const $container = $('<div/>');
        const rendererSpy = sinon.spy(i => `<div class="input"><input class="foo" id="foo--${i}"></div>`);
        const list = new ThingInputList(
            $container[0],
            '.foo',
            rendererSpy,
            {},
            {}
        );
        for (let i = 1; i <= 5; i++) {
            list.add_input();
            expect(rendererSpy.callCount).toBe(i);
            expect(rendererSpy.args[i-1][0]).toBe(i-1);
        }
    });

    test('remove_input removes children', () => {
        const $container = $('<div/>');
        const list = new ThingInputList(
            $container[0],
            '.foo',
            i => `<div class="input"><input class="foo" id="foo--${i}"></div>`,
            {},
            {}
        );
        for (let i = 1; i <= 5; i++) {
            list.add_input();
        }
        for (let i = 4; i > 0; i--) {
            list.remove_input($container[0].firstChild);
            expect($container.find('div.input').length).toBe(i);
        }
    });

    test('update_visible hides remove link when only 1 element', () => {
        const $container = $('<div/>');
        const list = new ThingInputList(
            $container[0],
            '.foo',
            i => `<div class="input"><input class="foo" id="foo--${i}"><a class="remove"/></div>`,
            {},
            {}
        );
        list.add_input();
        list.update_visible();
        expect($container.find('a.remove')[0].style.display).toBe('none');
    });

    test('update_visible shows remove link when more than 1 element', () => {
        const $container = $('<div/>');
        const children = 5;
        const list = new ThingInputList(
            $container[0],
            '.foo',
            i => `<div class="input"><input class="foo" id="foo--${i}"><a class="remove"/></div>`,
            {},
            {}
        );
        for (let i = 0; i < children; i++) {
            list.add_input();
        }
        list.update_visible();
        $container.find('a.remove').each((i, el) => {
            expect(el.style.display).not.toBe('none');
        });
    });

    test('update_visible hides last a.add', () => {
        const $container = $('<div/>');
        const children = 5;
        const list = new ThingInputList(
            $container[0],
            '.foo',
            i => `<div class="input"><input class="foo" id="foo--${i}"><a class="add"/></div>`,
            {},
            {}
        );
        for (let i = 0; i < children; i++) {
            list.add_input();
        }
        list.update_visible();
        $container.find('a.add').slice(0, -1).each((i, el) => {
            expect(el.style.display).toBe('none');
        });
        expect($container.find('a.add').slice(-1)[0].style.display).not.toBe('none');
    });
});


describe('ThingInput', () => {
    test('acceptValue updates -key element value', () => {
        $(document.body).html('<input id="author"><input id="author-key" value="old">');
        const sai = new ThingInput($('#author')[0], {}, {});
        sai.acceptValue({key: 'foo'});
        expect($('#author-key').val()).toBe('foo');
    });

    test('rejectValue empties -key element value', () => {
        $(document.body).html('<input id="author"><input id="author-key" value="old">');
        const sai = new ThingInput($('#author')[0], {}, {});
        sai.rejectValue();
        expect($('#author-key').val()).toBe('');
    });
});
