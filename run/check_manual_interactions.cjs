// Pure interaction logic checks; no browser or external packages required.
// Run: node run/check_manual_interactions.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../docs/manual/guide.js'), 'utf8');
function body(name, next) {
  return source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${next}(`));
}
function route(hash) {
  return JSON.parse(JSON.stringify(vm.runInNewContext(body('readRoute', 'navigate') + 'readRoute()', {
    location: { hash }, topics: ['video', 'range']
  })));
}
assert.deepEqual(route('#en/range/2/all'), { lang: 'en', topic: 'range', point: 2, all: true });
assert.deepEqual(route(''), { lang: 'ko', topic: 'video', point: 1, all: true });
assert.deepEqual(route('#ko/video/1/single'), { lang: 'ko', topic: 'video', point: 1, all: false });
assert.deepEqual(route('#xx/missing/-2'), { lang: 'ko', topic: 'video', point: 1, all: true });

const markers = ['2,18,25,5', '28,18,26,5'].map((box, i) => ({
  dataset: { box }, attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
  removeAttribute(name) { delete this.attributes[name]; }
  ,getAttribute() { return `#point-${i + 1}`; }
}));
// Deliberately reordered: grouping instructions must not break marker matching.
const details = [2, 1].map(n => ({ id: `point-${n}`, classList: {
  selected: false, toggle(name, value) { assert.equal(name, 'selected'); this.selected = value; }
} }));
const box = { style: {}, hidden: true };
const article = {
  querySelectorAll: selector => selector === '.hotspot' ? markers : details,
  querySelector: () => box
};
const context = vm.createContext({ article, state: { all: false } });
vm.runInContext(body('highlight', 'render') + 'highlight(article, 2)', context);
assert.deepEqual(details.map(d => d.classList.selected), [true, false]);
assert.deepEqual(box.style, { left: '28%', top: '18%', width: '26%', height: '5%' });
assert.equal(markers[1].attributes['aria-current'], 'step');
assert.equal(box.hidden, false);
vm.runInContext('highlight(article, 999)', context);
assert.deepEqual(details.map(d => d.classList.selected), [false, true]);
assert.equal(markers[1].attributes['aria-current'], undefined);
vm.runInContext('state.all = true; highlight(article, 2)', context);
assert.deepEqual(details.map(d => d.classList.selected), [true, false]);
assert.ok(details.every(d => !('open' in d)));
console.log('OK: language/topic routes, invalid links, numbered steps, highlight position, show-all state.');
