import streamlit as st
import os

st.set_page_config(
    page_title="SSTA's Filament Winder",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>

/* Main background */
.stApp {
    background-color: #080808;
    color: #FFFFFF;
}

/* Header */
header[data-testid="stHeader"] {
    background-color: #080808;
}

/* Main page spacing */
.block-container {
    max-width: 1500px;
    padding-top: 1rem;
    padding-bottom: 1rem;
}

/* SSTA title */
.menu-title {
    text-align: left;
    font-size: 48px;
    font-weight: 900;
    color: #FF8200;
    letter-spacing: 3px;
    line-height: 1.5;
    padding-top: 10px;
    margin-bottom: 0;
}

/* Subtitle */
.menu-subtitle {
    text-align: left;
    font-size: 15px;
    color: #AAAAAA;
    letter-spacing: 2px;
    margin-bottom: 25px;
}

/* Section headings */
.section-label {
    text-align: center;
    font-size: 22px;
    font-weight: bold;
    color: #FF8200;
    margin-bottom: 20px;
}

/* Input labels */
[data-testid="stWidgetLabel"] p {
    color: #FFFFFF !important;
    font-size: 16px;
}

/* Input fields */
[data-testid="stNumberInput"] input {
    background-color: #242424;
    color: #FFFFFF;
}

/* Buttons */
.stButton > button {
    width: 100%;
    background-color: #FF8200;
    color: #000000;
    border: 2px solid #FF8200;
    border-radius: 8px;
    font-size: 16px;
    font-weight: bold;
    min-height: 48px;
    transition: 0.2s;
}

.stButton > button:hover {
    background-color: #FFAA33;
    color: #000000;
    border-color: #FFAA33;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #111111;
}

</style>
""", unsafe_allow_html=True)


# -----------------------------
# SIDEBAR
# -----------------------------

with st.sidebar:
    st.title("Options")

# -----------------------------
# HEADER
# -----------------------------

st.markdown(
    '<div class="menu-title">SSTA</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="menu-subtitle">FILAMENT WINDER</div>',
    unsafe_allow_html=True
)

st.divider()

# -----------------------------
# MAIN MENU
# -----------------------------

left, middle, right = st.columns(
    [1.2, 1.5, 1],
    gap="large"
)

# -----------------------------
# LEFT: MANDREL SETTINGS
# -----------------------------

with left:
    st.markdown(
        '<div class="section-label">Mandrel Settings</div>',
        unsafe_allow_html=True
    )

    mandrel_length = st.number_input(
        "Mandrel Length",
        min_value=0.0,
        value=0.0,
        step=1.0
    )

    mandrel_diameter = st.number_input(
        "Mandrel Diameter",
        min_value=0.0,
        value=0.0,
        step=1.0
    )

    feed_rate = st.number_input(
        "Feed Rate",
        min_value=0.0,
        value=0.0,
        step=1.0
    )

# -----------------------------
# MIDDLE: LAYERS
# -----------------------------

with middle:
    st.markdown(
        '<div class="section-label">Layers</div>',
        unsafe_allow_html=True
    )

    if "layers" not in st.session_state:
        st.session_state.layers = []

    if st.button("+ Add Layer"):
        st.session_state.layers.append("Hoop")

    for i in range(len(st.session_state.layers)):
        st.session_state.layers[i] = st.selectbox(
            f"Layer {i + 1}",
            ["Hoop", "Helical"],
            index=["Hoop", "Helical"].index(
                st.session_state.layers[i]
            ),
            key=f"layer_{i}"
        )

# -----------------------------
# RIGHT: OUTPUT
# -----------------------------
@st.dialog("Winding Preview", width=250)
def show_winding_preview():
    st.image(
        os.path.join("Player Photos", "Preview.png"),
        width=250
    )

with right:
    st.markdown(
        '<div class="section-label">Output</div>',
        unsafe_allow_html=True
    )

    if st.button("Generate G-Code"):
        st.info("G-Code generation coming soon.")

    if st.button("Winding Preview"):
        show_winding_preview()
    st.image(
            os.path.join("Player Photos","SSTAmark.png" ),
            use_container_width=True
        )

# -----------------------------
# WINDING PREVIEW IMAGE
# -----------------------------

if st.session_state.get("show_preview", False):
    st.divider()

    st.markdown(
        '<div class="section-label">Winding Preview</div>',
        unsafe_allow_html=True
    )

    preview_path = os.path.join(
        os.path.dirname(__file__),
        "WindingPreview.png"
    )

    if os.path.exists(preview_path):
        
        st.image(
            preview_path,
            use_container_width=True
        )
    else:
        st.info("Add WindingPreview.png to the FilamentWinder folder.")
