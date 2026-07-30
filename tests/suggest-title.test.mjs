// Exercise the icon -> Title suggestion using the handlers as they are actually
// shipped: pickIcon and fieldChange are pulled out of the built page and run
// against a stub `this`, so this tests the patched source rather than a copy of
// its logic.
//
// Usage: node tests/suggest-title.test.mjs <built-index.html>
import fs from 'node:fs';

const built = process.argv[2];
const tpl = fs.readFileSync(built, 'utf8');

// Block-bodied handlers end at "\n  };"; titleFromUrl is a one-expression arrow
// spanning two lines, so it ends at the first ";" that closes the statement.
const grab = (name, arg = 'e') =>
  new RegExp('  ' + name + ' = (\\(' + arg + '\\) => \\{[\\s\\S]*?\\n  \\});').exec(tpl)[1];
const grabExpr = (name, arg) =>
  new RegExp('  ' + name + ' = (\\(' + arg + '\\) => [^;]*);').exec(tpl)[1];

// The handlers are class-property arrows, so they close over `this` lexically.
// Calling the factory with .call(ctx) makes that captured `this` the stub.
const factory = new Function(
  `return { pickIcon: ${grab('pickIcon')}, fieldChange: ${grab('fieldChange')},` +
  ` suggestFromUrl: ${grab('suggestFromUrl', 'm, raw')},` +
  ` labelFromUrl: ${grab('labelFromUrl', 'raw')},` +
  ` titleFromUrl: ${grabExpr('titleFromUrl', 'raw')} };`
);

// Enough of the real icon set to exercise "only filter to something that exists".
// synthtopia is deliberately absent, as it is from the real set — that is the
// case the site-icon fetch on save exists for.
const ICONS = ['gitea', 'grafana', 'plex', 'jellyfin', 'nginx', 'home-assistant',
  'gitea-light', 'portainer', 'proxmox'];

function session(modal, icons = ICONS) {
  const ctx = {
    state: { icons, modal: { icon: '', title: '', desc: '', url: '', q: '', ...modal } },
    setState(u) {
      Object.assign(ctx.state, typeof u === 'function' ? u(ctx.state) : u);
    },
  };
  const h = factory.call(ctx);
  // fieldChange calls this.suggestFromUrl, which calls the other two, and the
  // arrows captured `this` as ctx — so they have to hang off ctx, not just off
  // the object the factory returns.
  Object.assign(ctx, h);
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

// 8. the URL also pre-filters the icon grid, by filling the search box
const urlBrowse = session();
urlBrowse.type('url', 'http://gitea.lan');
check('a URL pre-filters the grid', q(urlBrowse.modal) === 'gitea', q(urlBrowse.modal));
check('and the search box shows why', urlBrowse.modal.q === 'gitea', urlBrowse.modal.q);

// Picking from that filtered grid must leave it exactly as it is — the search
// box is what filters, so the title flipping to the icon's own guess changes
// nothing.
const stable = session();
stable.type('url', 'http://gitea.lan');
stable.pick('gitea-light');
check('picking an icon does not unfilter the grid', q(stable.modal) === 'gitea', q(stable.modal));
check('and the icon still suggests the title', stable.modal.title === 'Gitea', stable.modal.title);

// A label no icon matches must leave the grid alone rather than empty it.
const nohit = session();
nohit.type('url', 'http://my-nas.lan');
check('an unmatched label does not filter', q(nohit.modal) === '', q(nohit.modal));
check('but it still suggests the title', nohit.modal.title === 'My Nas', nohit.modal.title);

// A search the user typed is theirs.
const typedSearch = session();
typedSearch.type('q', 'jelly');
typedSearch.type('url', 'http://gitea.lan');
check('a typed search survives a URL', typedSearch.modal.q === 'jelly', typedSearch.modal.q);
check('the title is still suggested', typedSearch.modal.title === 'Gitea', typedSearch.modal.title);

// A later URL replaces a search this dialog filled in.
const refilter = session();
refilter.type('url', 'http://gitea.lan');
refilter.type('url', 'http://grafana.lan');
check('a changed URL re-filters the grid', refilter.modal.q === 'grafana', refilter.modal.q);
const unfilter = session();
unfilter.type('url', 'http://gitea.lan');
unfilter.type('url', 'http://my-nas.lan');
check('a URL matching nothing clears the old filter', unfilter.modal.q === '', unfilter.modal.q);
const cleared2 = session();
cleared2.type('url', 'http://gitea.lan');
cleared2.type('url', '');
check('clearing the URL clears the filter', cleared2.modal.q === '', cleared2.modal.q);

// With no icons loaded yet, nothing matches and the grid is left alone.
const noIcons = session(undefined, []);
noIcons.type('url', 'http://gitea.lan');
check('no icon list means no filter', noIcons.modal.q === '', noIcons.modal.q);
check('the title is suggested regardless', noIcons.modal.title === 'Gitea', noIcons.modal.title);

// 9. the URL also selects the icon, so Enter stores it
const picked = session();
picked.type('url', 'http://gitea.lan');
check('a URL selects the matching icon', picked.modal.icon === 'gitea', picked.modal.icon);

// Exact names only. The grid matches substrings, so the first icon shown for
// "plex" is perplexity-dark and for "nginx" it is nginx-proxy-manager — taking
// the first would attach the wrong icon and Enter would store it.
const SUBSTRING_TRAPS = ['perplexity-dark', 'plex', 'nginx-proxy-manager', 'nginx',
  'freenas', 'nasa', 'habitica', 'jellyfin-vue', 'jellyfin'];
for (const [url, want] of [
  ['http://plex.lan', 'plex'],
  ['http://nginx.lan', 'nginx'],
  ['http://jellyfin.lan', 'jellyfin'],
]) {
  const s = session(undefined, SUBSTRING_TRAPS);
  s.type('url', url);
  check(`${url} picks ${want}, not the first substring match`, s.modal.icon === want, s.modal.icon);
}
for (const url of ['http://ha.lan', 'http://nas.lan']) {
  const s = session(undefined, SUBSTRING_TRAPS);
  s.type('url', url);
  check(`${url} selects nothing rather than guessing`, !s.modal.icon, s.modal.icon);
}

// A host with no icon at all leaves it for the site-icon fetch on save.
const noMatch = session();
noMatch.type('url', 'https://synthtopia.com');
check('an unmatched host selects no icon', !noMatch.modal.icon, noMatch.modal.icon);

// A hand-picked icon is a decision: later URLs refine the title, never the icon.
const handPicked = session();
handPicked.pick('plex');
handPicked.type('url', 'http://gitea.lan');
check('a hand-picked icon survives a URL', handPicked.modal.icon === 'plex', handPicked.modal.icon);
check('but the title still follows the URL', handPicked.modal.title === 'Gitea', handPicked.modal.title);

// An auto-selected one is replaced by the next URL, and dropped when nothing matches.
const reselect = session();
reselect.type('url', 'http://gitea.lan');
reselect.type('url', 'http://grafana.lan');
check('a changed URL re-selects the icon', reselect.modal.icon === 'grafana', reselect.modal.icon);
const dropped = session();
dropped.type('url', 'http://gitea.lan');
dropped.type('url', 'https://synthtopia.com');
check('a URL matching no icon drops the auto one', !dropped.modal.icon, dropped.modal.icon);
const clearedIcon = session();
clearedIcon.type('url', 'http://gitea.lan');
clearedIcon.type('url', '');
check('clearing the URL drops the auto icon', !clearedIcon.modal.icon, clearedIcon.modal.icon);

// Editing an existing entry must not have its icon swapped under it.
const editIcon = session({ icon: 'plex', title: 'Plex', url: 'http://plex.lan' });
editIcon.type('url', 'http://gitea.lan');
check('an existing icon survives a URL change', editIcon.modal.icon === 'plex', editIcon.modal.icon);

// Junk in must not throw.
for (const junk of ['', '   ', 'http://', 'https://.', 'not a url', '::::', 'http://%zz.lan']) {
  const s = session();
  let threw = null;
  try { s.type('url', junk); } catch (e) { threw = e.message; }
  check(`${JSON.stringify(junk)} does not throw`, !threw, threw);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
