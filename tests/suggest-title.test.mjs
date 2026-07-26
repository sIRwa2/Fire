// Exercise the icon -> Title suggestion using the handlers as they are actually
// shipped: pickIcon and fieldChange are pulled out of the built page and run
// against a stub `this`, so this tests the patched source rather than a copy of
// its logic.
//
// Usage: node tests/suggest-title.test.mjs <built-index.html>
import fs from 'node:fs';

const built = process.argv[2];
const tpl = JSON.parse(
  /<script type="__bundler\/template">([\s\S]*?)<\/script>/.exec(
    fs.readFileSync(built, 'utf8')
  )[1]
);

const grab = (name) =>
  new RegExp('  ' + name + ' = (\\(e\\) => \\{[\\s\\S]*?\\n  \\});').exec(tpl)[1];

// The handlers are class-property arrows, so they close over `this` lexically.
// Calling the factory with .call(ctx) makes that captured `this` the stub.
const factory = new Function(
  `return { pickIcon: ${grab('pickIcon')}, fieldChange: ${grab('fieldChange')} };`
);

function session(modal) {
  const ctx = {
    state: { modal: { icon: '', title: '', desc: '', url: '', q: '', ...modal } },
    setState(u) {
      Object.assign(ctx.state, typeof u === 'function' ? u(ctx.state) : u);
    },
  };
  const h = factory.call(ctx);
  return {
    pick: (icon) => h.pickIcon({ currentTarget: { dataset: { icon } } }),
    type: (field, value) =>
      h.fieldChange({ currentTarget: { dataset: { field }, value } }),
    get modal() { return ctx.state.modal; },
  };
}

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  cond ? pass++ : fail++;
  console.log(`${cond ? 'ok  ' : 'FAIL'}  ${name}${cond ? '' : '  <- ' + extra}`);
};

// 1. proper case from the kebab-case icon slugs
for (const [icon, want] of [
  ['plex', 'Plex'],
  ['home-assistant', 'Home Assistant'],
  ['adguard-home', 'Adguard Home'],
  ['vim-dark', 'Vim'],              // variant suffix dropped
  ['koillection-light', 'Koillection'],
  ['proton-calendar', 'Proton Calendar'],
]) {
  const s = session();
  s.pick(icon);
  check(`${icon} -> ${want}`, s.modal.title === want, s.modal.title);
  check(`${icon} sets the icon`, s.modal.icon === icon, s.modal.icon);
}

// 2. it is a suggestion, not an overwrite
const typed = session();
typed.type('title', 'My Media Box');
typed.pick('plex');
check('a typed title is never overwritten', typed.modal.title === 'My Media Box', typed.modal.title);

// 3. trying another icon updates a title that came from a previous pick
const retry = session();
retry.pick('plex');
retry.pick('jellyfin');
check('a suggested title follows later picks', retry.modal.title === 'Jellyfin', retry.modal.title);

// 4. once edited by hand, later picks leave it alone
const edited = session();
edited.pick('plex');
edited.type('title', 'Plex Server');
edited.pick('jellyfin');
check('editing releases the suggestion', edited.modal.title === 'Plex Server', edited.modal.title);

// 5. editing an existing app keeps its title
const existing = session({ icon: 'plex', title: 'Plex' });
existing.pick('jellyfin');
check('an existing title survives an icon change', existing.modal.title === 'Plex', existing.modal.title);

// 6. the icon grid must not re-filter on an autofilled title. The page computes
//    q = (m.q || (m.titleAuto ? '' : m.title) || ''), so mirror that here.
const q = (m) => (m.q || (m.titleAuto ? '' : m.title) || '').trim().toLowerCase();
const browse = session();
browse.pick('plex');
check('autofilled title leaves the grid unfiltered', q(browse.modal) === '', q(browse.modal));
const searched = session();
searched.type('q', 'jelly');
searched.pick('jellyfin');
check('an active search is preserved', q(searched.modal) === 'jelly', q(searched.modal));
const titleFirst = session();
titleFirst.type('title', 'Plex');
check('typing a title still pre-filters the grid', q(titleFirst.modal) === 'plex', q(titleFirst.modal));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
