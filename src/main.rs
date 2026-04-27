use std::collections::{BTreeMap, BTreeSet};
use std::rc::Rc;

use gloo_net::http::Request;
use serde::Deserialize;
use wasm_bindgen::JsCast;
use web_sys::{HtmlInputElement, HtmlSelectElement};
use yew::prelude::*;

const PAGE_SIZE: usize = 60;

#[derive(Clone, PartialEq, Deserialize, Debug)]
pub struct Base {
    pub id: String,
    pub name: String,
    pub town_hall: u8,
    #[serde(rename = "type")]
    pub base_type: String,
    pub link: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub builder: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub added: String,
    #[serde(default)]
    pub image: Option<String>,
}

#[derive(Deserialize)]
struct BaseFile {
    bases: Vec<Base>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ThTab {
    All,
    Th(u8),
}

#[function_component(App)]
fn app() -> Html {
    // `bases` is wrapped in Rc so cloning the state handle on each render
    // does not deep-copy the (5_000+ entry) vector.
    let bases = use_state(|| Rc::<Vec<Base>>::new(Vec::new()));
    let loading = use_state(|| true);
    let error = use_state(|| Option::<String>::None);
    let tab = use_state(|| Option::<ThTab>::None);
    let filter_type = use_state(String::new);
    let search = use_state(String::new);
    let visible = use_state(|| PAGE_SIZE);

    {
        let bases = bases.clone();
        let loading = loading.clone();
        let error = error.clone();
        let tab = tab.clone();
        use_effect_with((), move |_| {
            wasm_bindgen_futures::spawn_local(async move {
                match Request::get("bases.json").send().await {
                    Ok(resp) => match resp.json::<BaseFile>().await {
                        Ok(bf) => {
                            let mut list = bf.bases;
                            list.sort_by(|a, b| {
                                b.town_hall
                                    .cmp(&a.town_hall)
                                    .then_with(|| a.name.cmp(&b.name))
                            });
                            // Default to the highest TH that has bases.
                            let default_tab = list
                                .iter()
                                .map(|b| b.town_hall)
                                .max()
                                .map(ThTab::Th)
                                .unwrap_or(ThTab::All);
                            tab.set(Some(default_tab));
                            bases.set(Rc::new(list));
                            loading.set(false);
                        }
                        Err(e) => {
                            error.set(Some(format!("Could not parse bases.json: {e}")));
                            loading.set(false);
                        }
                    },
                    Err(e) => {
                        error.set(Some(format!("Could not load bases.json: {e}")));
                        loading.set(false);
                    }
                }
            });
            || ()
        });
    }

    // Derived collections — memoised against the bases vector.  Without this,
    // every keystroke in the search box would walk all 5k entries to rebuild
    // the type list and TH counts.
    let counts_per_th = use_memo(bases.clone(), |bs| {
        let mut m: BTreeMap<u8, usize> = BTreeMap::new();
        for b in bs.iter() {
            *m.entry(b.town_hall).or_default() += 1;
        }
        m
    });
    let town_halls: Vec<u8> = counts_per_th.keys().rev().copied().collect();
    let types = use_memo(bases.clone(), |bs| {
        let s: BTreeSet<&str> = bs.iter().map(|b| b.base_type.as_str()).collect();
        s.into_iter().map(str::to_owned).collect::<Vec<_>>()
    });

    let active_tab = tab.unwrap_or(ThTab::All);

    // Reset pagination back to PAGE_SIZE whenever the filters change.
    {
        let visible = visible.clone();
        let deps = (active_tab, (*filter_type).clone(), (*search).clone());
        use_effect_with(deps, move |_| {
            visible.set(PAGE_SIZE);
            || ()
        });
    }

    let q = (*search).trim().to_lowercase();
    let filter_type_str: &str = &filter_type;

    let matches: Vec<&Base> = bases
        .iter()
        .filter(|b| match active_tab {
            ThTab::All => true,
            ThTab::Th(t) => b.town_hall == t,
        })
        .filter(|b| filter_type_str.is_empty() || b.base_type.eq_ignore_ascii_case(filter_type_str))
        .filter(|b| {
            if q.is_empty() {
                return true;
            }
            b.name.to_lowercase().contains(&q)
                || b.builder.to_lowercase().contains(&q)
                || b.description.to_lowercase().contains(&q)
                || b.tags.iter().any(|t| t.to_lowercase().contains(&q))
        })
        .collect();

    let total_matches = matches.len();
    let shown = (*visible).min(total_matches);
    let visible_slice = &matches[..shown];

    let on_search = {
        let search = search.clone();
        Callback::from(move |e: InputEvent| {
            if let Some(input) = e.target().and_then(|t| t.dyn_into::<HtmlInputElement>().ok()) {
                search.set(input.value());
            }
        })
    };
    let on_type_change = {
        let filter_type = filter_type.clone();
        Callback::from(move |e: Event| {
            if let Some(sel) = e.target().and_then(|t| t.dyn_into::<HtmlSelectElement>().ok()) {
                filter_type.set(sel.value());
            }
        })
    };

    let on_pick = |t: ThTab| {
        let tab = tab.clone();
        Callback::from(move |_: MouseEvent| tab.set(Some(t)))
    };

    let on_load_more = {
        let visible = visible.clone();
        Callback::from(move |_: MouseEvent| {
            visible.set(*visible + PAGE_SIZE);
        })
    };

    let total = bases.len();

    let body = if *loading {
        html! { <p class="status">{ "Loading bases…" }</p> }
    } else if let Some(err) = (*error).clone() {
        html! { <p class="status error">{ err }</p> }
    } else if bases.is_empty() {
        html! {
            <div class="status">
                <p>{ "No bases have been shared yet." }</p>
                <p>
                    { "Open a pull request to add an entry to " }
                    <code>{ "bases.json" }</code>
                    { " — see the README for the format." }
                </p>
            </div>
        }
    } else if total_matches == 0 {
        html! { <p class="status">{ "No bases match your filters." }</p> }
    } else {
        let load_more = if shown < total_matches {
            let remaining = total_matches - shown;
            html! {
                <div class="load-more-wrap">
                    <button class="btn" onclick={on_load_more}>
                        { format!("Show more ({remaining} remaining)") }
                    </button>
                </div>
            }
        } else {
            Html::default()
        };
        html! {
            <>
                <div class="grid">
                    { for visible_slice.iter().map(|b| html! {
                        <BaseCard key={b.id.clone()} base={(*b).clone()} />
                    }) }
                </div>
                { load_more }
            </>
        }
    };

    html! {
        <>
            <header class="hero">
                <h1>{ "Clash Bases" }</h1>
                <p class="tagline">
                    { "A community library of Clash of Clans base layouts. \
                       Browse, search, and open any base directly in-game." }
                </p>
            </header>

            if !bases.is_empty() {
                <nav class="tabs" role="tablist" aria-label="Filter by Town Hall level">
                    <button
                        class={classes!("tab", (active_tab == ThTab::All).then_some("active"))}
                        onclick={on_pick(ThTab::All)}
                        role="tab"
                        aria-selected={(active_tab == ThTab::All).to_string()}
                    >
                        { "All " }
                        <span class="tab-count">{ total }</span>
                    </button>
                    { for town_halls.iter().map(|th| {
                        let t = ThTab::Th(*th);
                        let n = counts_per_th.get(th).copied().unwrap_or(0);
                        html! {
                            <button
                                class={classes!("tab", (active_tab == t).then_some("active"))}
                                onclick={on_pick(t)}
                                role="tab"
                                aria-selected={(active_tab == t).to_string()}
                            >
                                { format!("TH{th} ") }
                                <span class="tab-count">{ n }</span>
                            </button>
                        }
                    }) }
                </nav>
            }

            <section class="controls">
                <input
                    type="text"
                    placeholder="Search by name, builder, tag…"
                    aria-label="Search bases by name, builder or tag"
                    autocomplete="off"
                    spellcheck="false"
                    value={(*search).clone()}
                    oninput={on_search}
                />
                <select onchange={on_type_change} aria-label="Filter by base type">
                    <option value="" selected={filter_type.is_empty()}>{ "All Types" }</option>
                    { for types.iter().map(|t| html! {
                        <option value={t.clone()} selected={*filter_type == *t}>{ t.clone() }</option>
                    }) }
                </select>
                <span class="count" aria-live="polite">
                    { format!("{shown} of {total_matches} base(s)") }
                </span>
            </section>

            <main>{ body }</main>

            <footer>
                <p>
                    { "Bases live in " }
                    <code>{ "bases.json" }</code>
                    { " — " }
                    <a href="https://github.com/nschmeller/clash-bases" target="_blank" rel="noopener noreferrer">
                        { "view source on GitHub" }
                    </a>
                    { " or open a pull request to add yours." }
                </p>
            </footer>
        </>
    }
}

#[derive(Properties, PartialEq)]
struct BaseCardProps {
    base: Base,
}

#[function_component(BaseCard)]
fn base_card(props: &BaseCardProps) -> Html {
    let copied = use_state(|| false);
    let b = &props.base;

    let on_copy = {
        let link = b.link.clone();
        let copied = copied.clone();
        Callback::from(move |_: MouseEvent| {
            let link = link.clone();
            let copied = copied.clone();
            wasm_bindgen_futures::spawn_local(async move {
                if let Some(win) = web_sys::window() {
                    let clipboard = win.navigator().clipboard();
                    let promise = clipboard.write_text(&link);
                    let _ = wasm_bindgen_futures::JsFuture::from(promise).await;
                    copied.set(true);
                    gloo_timers::callback::Timeout::new(1500, move || copied.set(false)).forget();
                }
            });
        })
    };

    let badge_class = format!("th-badge th-{}", b.town_hall);

    html! {
        <article class="card">
            if let Some(img) = &b.image {
                <a class="thumb" href={b.link.clone()} target="_blank" rel="noopener noreferrer">
                    <img src={img.clone()} alt={format!("Preview of {}", &b.name)} loading="lazy" decoding="async" />
                </a>
            }
            <div class="card-header">
                <span class={badge_class}>{ format!("TH{}", b.town_hall) }</span>
                <span class="type">{ &b.base_type }</span>
            </div>
            <h2>{ &b.name }</h2>
            if !b.builder.is_empty() {
                <p class="builder">{ "by " }<strong>{ &b.builder }</strong></p>
            }
            if !b.description.is_empty() {
                <p class="desc">{ &b.description }</p>
            }
            if !b.tags.is_empty() {
                <ul class="tags">
                    { for b.tags.iter().map(|t| html! { <li>{ t }</li> }) }
                </ul>
            }
            <div class="actions">
                <a class="btn primary"
                   href={b.link.clone()}
                   target="_blank"
                   rel="noopener noreferrer"
                   aria-label={format!("Open {} in Clash of Clans", &b.name)}>
                    { "Open in Clash" }
                </a>
                <button class="btn"
                        onclick={on_copy}
                        aria-live="polite"
                        aria-label={format!("Copy share link for {}", &b.name)}>
                    { if *copied { "Copied!" } else { "Copy link" } }
                </button>
            </div>
        </article>
    }
}

fn main() {
    yew::Renderer::<App>::new().render();
}
