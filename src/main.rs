use std::collections::BTreeSet;

use gloo_net::http::Request;
use serde::Deserialize;
use wasm_bindgen::JsCast;
use web_sys::{HtmlInputElement, HtmlSelectElement};
use yew::prelude::*;

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
}

#[derive(Deserialize)]
struct BaseFile {
    bases: Vec<Base>,
}

#[function_component(App)]
fn app() -> Html {
    let bases = use_state(Vec::<Base>::new);
    let loading = use_state(|| true);
    let error = use_state(|| Option::<String>::None);
    let filter_th = use_state(|| Option::<u8>::None);
    let filter_type = use_state(String::new);
    let search = use_state(String::new);

    {
        let bases = bases.clone();
        let loading = loading.clone();
        let error = error.clone();
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
                            bases.set(list);
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

    let town_halls: Vec<u8> = {
        let s: BTreeSet<u8> = bases.iter().map(|b| b.town_hall).collect();
        let mut v: Vec<u8> = s.into_iter().collect();
        v.sort_by(|a, b| b.cmp(a));
        v
    };
    let types: Vec<String> = {
        let s: BTreeSet<String> = bases.iter().map(|b| b.base_type.clone()).collect();
        s.into_iter().collect()
    };

    let filtered: Vec<Base> = bases
        .iter()
        .filter(|b| filter_th.map_or(true, |th| b.town_hall == th))
        .filter(|b| filter_type.is_empty() || b.base_type.eq_ignore_ascii_case(&filter_type))
        .filter(|b| {
            let q = search.trim().to_lowercase();
            if q.is_empty() {
                return true;
            }
            b.name.to_lowercase().contains(&q)
                || b.builder.to_lowercase().contains(&q)
                || b.description.to_lowercase().contains(&q)
                || b.tags.iter().any(|t| t.to_lowercase().contains(&q))
        })
        .cloned()
        .collect();

    let on_search = {
        let search = search.clone();
        Callback::from(move |e: InputEvent| {
            if let Some(input) = e.target().and_then(|t| t.dyn_into::<HtmlInputElement>().ok()) {
                search.set(input.value());
            }
        })
    };
    let on_th_change = {
        let filter_th = filter_th.clone();
        Callback::from(move |e: Event| {
            if let Some(sel) = e.target().and_then(|t| t.dyn_into::<HtmlSelectElement>().ok()) {
                let v = sel.value();
                filter_th.set(if v.is_empty() { None } else { v.parse().ok() });
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
    } else if filtered.is_empty() {
        html! { <p class="status">{ "No bases match your filters." }</p> }
    } else {
        html! {
            <div class="grid">
                { for filtered.iter().map(|b| html! { <BaseCard base={b.clone()} /> }) }
            </div>
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

            <section class="controls">
                <input
                    type="text"
                    placeholder="Search by name, builder, tag…"
                    value={(*search).clone()}
                    oninput={on_search}
                />
                <select onchange={on_th_change}>
                    <option value="" selected={filter_th.is_none()}>{ "All Town Halls" }</option>
                    { for town_halls.iter().map(|th| html! {
                        <option value={th.to_string()} selected={filter_th.map_or(false, |x| x == *th)}>
                            { format!("TH{}", th) }
                        </option>
                    }) }
                </select>
                <select onchange={on_type_change}>
                    <option value="" selected={filter_type.is_empty()}>{ "All Types" }</option>
                    { for types.iter().map(|t| html! {
                        <option value={t.clone()} selected={*filter_type == *t}>{ t.clone() }</option>
                    }) }
                </select>
                <span class="count">{ format!("{} base(s)", filtered.len()) }</span>
            </section>

            <main>{ body }</main>

            <footer>
                <p>
                    { "Bases are stored in " }
                    <code>{ "bases.json" }</code>
                    { " in this repository. Open a pull request to add yours." }
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
                <a class="btn primary" href={b.link.clone()} target="_blank" rel="noopener noreferrer">
                    { "Open in Clash" }
                </a>
                <button class="btn" onclick={on_copy}>
                    { if *copied { "Copied!" } else { "Copy link" } }
                </button>
            </div>
        </article>
    }
}

fn main() {
    yew::Renderer::<App>::new().render();
}
