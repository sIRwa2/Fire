// Exercise the icon -> Title suggestion using the handlers as they are actually
// shipped: pickIcon and fieldChange are pulled out of the built page and run
// against a stub `this`, so this tests the patched source rather than a copy of
// its logic.
//
// Usage: node tests/suggest-title.test.mjs <built-index.html>
import fs from 'node:fs';

const built = process.argv[2];
const tpl = fs.readFileSync(built, 'utf8');

const grab = (name, arg = 'e') =>
  new RegExp('  ' + name + ' = (\\(' + arg + '\\) => \\{[\\s\\S]*?\\n  \\});').exec(tpl)[1];

// The handlers are class-property arrows, so they close over `this` lexically.
// Calling the factory with .call(ctx) makes that captured `this` the stub.
const factory = new Function(
  `return { pickIcon: ${grab('pickIcon')}, fieldChange: ${grab('fieldChange')},` +
  ` titleFromUrl: ${grab('titleFromUrl', 'raw')} };`
);

function session(modal) {
  const ctx = {
    state: { modal: { icon: '', title: '', desc: '', url: '', q: '', ...modal } },
    setState(u) {
      Object.assign(ctx.state, typeof u === 'function' ? u(ctx.state) : u);
    },
  };
  const h = factory.call(ctx);
  // fieldChange calls this.titleFromUrl, and the arrows captured `this` as ctx —
  // so the helper has to hang off ctx, not just off the returned object.
  ctx.titleFromUrl = h.titleFromUrl;
  return {
    pick: (icon) => h.pickIcon({ currentTarget: { dataset: { icon } } }),
    type: (field, value) =>
      h.fieldChange({ currentTarget: { dataset: { field }, value } }),
    fromUrl: h.titleFromUrl,
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

// 7. the other direction: a URL suggests a Title
for (const [url, want] of [
  ['http://plex.lan', 'Plex'],
  ['plex.lan', 'Plex'],                                  // scheme-less paste
  ['https://www.motogp.com/en/videopass', 'Motogp'],     // leading www. dropped
  ['https://jellyfin.example.com', 'Jellyfin'],
  ['http://home-assistant.lan:8123', 'Home Assistant'],  // port ignored
  ['http://nas.lan:5000/webman/index.cgi', 'Nas'],       // path ignored when the host names it
  ['https://synthtopia.com/', 'Synthtopia'],
  ['http://192.168.0.108:3002/gitea', 'Gitea'],          // IP -> first path segment
  ['http://192.168.0.108:3002/gitea/firehome.git', 'Gitea'],
  ['http://[::1]:8096/jellyfin', 'Jellyfin'],            // IPv6 too
]) {
  const s = session();
  s.type('url', url);
  check(`${url} -> ${want}`, s.modal.title === want, s.modal.title);
}

// Nothing to go on: no host name and no path. Better an empty field than junk.
for (const url of ['http://192.168.0.108:8080', 'http://192.168.0.108/', 'http://10.0.0.5/2024']) {
  const s = session();
  s.type('url', url);
  check(`${url} suggests nothing`, s.modal.title === '', s.modal.title);
}

// Mid-typing must not dribble G, Gi, Git into the field: the guess waits for a
// scheme, a dot or a colon.
const typing = session();
for (const partial of ['g', 'gi', 'git']) {
  typing.type('url', partial);
  check(`"${partial}" is too early to guess`, typing.modal.title === '', typing.modal.title);
}
typing.type('url', 'gitea.lan');
check('a host-shaped URL then guesses', typing.modal.title === 'Gitea', typing.modal.title);

// The same rules as the icon suggestion: a typed title wins, an auto one updates.
const owned = session();
owned.type('title', 'My Git');
owned.type('url', 'http://gitea.lan');
check('a typed title survives a URL', owned.modal.title === 'My Git', owned.modal.title);

const refined = session();
refined.type('url', 'http://gitea.lan');
refined.type('url', 'http://grafana.lan');
check('a guessed title follows a changed URL', refined.modal.title === 'Grafana', refined.modal.title);

const cleared = session();
cleared.type('url', 'http://gitea.lan');
cleared.type('url', '');
check('clearing the URL clears the guess', cleared.modal.title === '', cleared.modal.title);

const kept = session();
kept.type('title', 'Mine');
kept.type('url', 'http://gitea.lan');
kept.type('url', '');
check('clearing the URL keeps a typed title', kept.modal.title === 'Mine', kept.modal.title);

// Editing an existing entry must not have its title rewritten under it.
const edit = session({ icon: 'plex', title: 'Plex', url: 'http://plex.lan' });
edit.type('url', 'http://plex.home.arpa');
check('an existing title survives a URL change', edit.modal.title === 'Plex', edit.modal.title);

// The two suggestions cooperate, in either order.
const urlThenIcon = session();
urlThenIcon.type('url', 'http://gitea.lan');
urlThenIcon.pick('jellyfin');
check('a later icon pick refines the URL guess',
  urlThenIcon.modal.title === 'Jellyfin', urlThenIcon.modal.title);
const iconThenUrl = session();
iconThenUrl.pick('jellyfin');
iconThenUrl.type('url', 'http://gitea.lan');
check('a later URL refines the icon guess',
  iconThenUrl.modal.title === 'Gitea', iconThenUrl.modal.title);

// A URL-suggested title must not collapse the icon grid either.
const urlBrowse = session();
urlBrowse.type('url', 'http://gitea.lan');
check('a URL guess leaves the grid unfiltered', q(urlBrowse.modal) === '', q(urlBrowse.modal));

// Junk in must not throw.
for (const junk of ['', '   ', 'http://', 'https://.', 'not a url', '::::', 'http://%zz.lan']) {
  const s = session();
  let threw = null;
  try { s.type('url', junk); } catch (e) { threw = e.message; }
  check(`${JSON.stringify(junk)} does not throw`, !threw, threw);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
