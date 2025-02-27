import streamlit as st
import google.generativeai as genai
import base64
import json
import io
import fitz  # PyMuPDF
from PIL import Image
import os
import re
from datetime import datetime
import pandas as pd
import csv

# Constants
MAPPING_FILE = "payee_mappings.csv"
MAPPING_COLUMNS = ['Full Name', 'Short Form']

def generate_prompt(override_prompt: str = "") -> str:
    if override_prompt:
        return override_prompt

    prompt = """
    Extract the following information from this e-cheque and return it as JSON. For the currency field, 
    please normalize it according to these rules:
    - '¥' or '￥' or 'RMB' should be normalized to 'CNY'
    - '$' or 'USD' or 'US$' should be normalized to 'USD'
    - 'HK$' or 'HKD' should be normalized to 'HKD'
    - '€' should be normalized to 'EUR'
    - '£' should be normalized to 'GBP'

    Also, analyze the remarks field to determine if this is:
    1. A trailer fee payment (includes any mention of trailer, rebate for trailer, etc.)
    2. A management fee payment (only for OFS/Oreana Financial Services, includes managed services fee, management fee, etc.)

    Schema:
    {
      "type": "object",
      "properties": {
        "bank_name": { "type": "string", "description": "The name of the bank issuing the e-cheque." },
        "date": { "type": "string", "format": "date", "description": "The date the e-cheque was issued (YYYY-MM-DD)." },
        "payee": { "type": "string", "description": "The name of the person or entity to whom the e-cheque is payable." },
        "payer": { "type": "string", "description": "The name of the account the funds are drawn from." },
        "amount_numerical": { "type": "string", "description": "The amount of the e-cheque in numerical form (e.g., 66969.77)." },
        "amount_words": { "type": "string", "description": "The amount of the e-cheque in words." },
        "cheque_number": { "type": "string", "description": "The full cheque number, including all digits and spaces." },
        "key_identifier": { "type": "string", "description": "The first six digits of the cheque number." },
        "currency": { "type": "string", "description": "The normalized currency code (CNY, USD, HKD, EUR, GBP)"},
        "remarks": { "type": "string", "description": "The remark of the e-cheque"},
        "is_trailer_fee": { "type": "boolean", "description": "True if this is a trailer fee payment based on remarks" },
        "is_management_fee": { "type": "boolean", "description": "True if this is a management fee payment for OFS/Oreana" },
        "next_step": { "type": "string" }
      },
      "required": ["date", "payee", "amount_numerical", "key_identifier", "payer", "next_step", "is_trailer_fee", "is_management_fee"]
    }

    Rules for next_step determination:
    1. If the 'remarks' field contains "URGENT", set 'next_step' to 'Flag for Manual Review'
    2. If the 'currency' is not 'HKD', set 'next_step' to 'Flag for Manual Review'
    3. Otherwise, set 'next_step' to 'Process Payment'

    Return only the JSON object with no additional text or formatting.
    """
    return prompt

def load_mappings():
    try:
        if os.path.exists(MAPPING_FILE):
            df = pd.read_csv(MAPPING_FILE)
        else:
            df = pd.DataFrame(columns=MAPPING_COLUMNS)
        return df
    except Exception as e:
        st.error(f"Error loading mappings: {e}")
        return pd.DataFrame(columns=MAPPING_COLUMNS)

def save_mappings(df):
    try:
        df.to_csv(MAPPING_FILE, index=False)
    except Exception as e:
        st.error(f"Error saving mappings: {e}")

def get_payee_shortform(payee: str, mappings_df: pd.DataFrame) -> str:
    if mappings_df.empty:
        return payee
        
    payee_upper = payee.upper().strip()
    # Remove extra spaces between words and standardize spaces
    payee_upper = ' '.join(payee_upper.split())
    
    # Do the same standardization for the mapping names
    mappings_df['Standardized_Name'] = mappings_df['Full Name'].str.upper().str.strip().apply(lambda x: ' '.join(x.split()))
    
    match = mappings_df[mappings_df['Standardized_Name'] == payee_upper]
    if not match.empty:
        return match.iloc[0]['Short Form']
    return payee

def show_mapping_manager():
    st.sidebar.header("Payee Mapping Manager")
    
    # Load existing mappings
    mappings_df = load_mappings()
    
    # Add new mapping
    with st.sidebar.expander("Add New Mapping"):
        with st.form("add_mapping"):
            full_name = st.text_input("Full Name").strip()
            short_form = st.text_input("Short Form").strip()
            submitted = st.form_submit_button("Add Mapping")
            
            if submitted and full_name and short_form:
                if not mappings_df[mappings_df['Full Name'].str.upper() == full_name.upper()].empty:
                    st.error("This company name already exists!")
                else:
                    new_row = pd.DataFrame([[full_name, short_form]], columns=MAPPING_COLUMNS)
                    mappings_df = pd.concat([mappings_df, new_row], ignore_index=True)
                    save_mappings(mappings_df)
                    st.success("Mapping added successfully!")

    # Display and manage existing mappings
    st.sidebar.subheader("Existing Mappings")
    
    # Add filter
    filter_text = st.sidebar.text_input("🔍 Filter mappings", "").strip().upper()
    
    # Sort mappings
    sort_column = st.sidebar.radio("Sort by:", ["Full Name", "Short Form"])
    sort_order = st.sidebar.radio("Sort order:", ["Ascending", "Descending"])
    
    if not mappings_df.empty:
        # Apply sorting
        mappings_df = mappings_df.sort_values(
            by=sort_column,
            ascending=(sort_order == "Ascending")
        )
        
        # Apply filtering
        if filter_text:
            mask = (mappings_df['Full Name'].str.upper().str.contains(filter_text) |
                   mappings_df['Short Form'].str.upper().str.contains(filter_text))
            filtered_df = mappings_df[mask]
        else:
            filtered_df = mappings_df
        
        # Display mappings in a more organized way
        st.sidebar.markdown("---")
        if filtered_df.empty:
            st.sidebar.info("No mappings match your filter.")
        else:
            for idx, row in filtered_df.iterrows():
                with st.sidebar.container():
                    col1, col2, col3 = st.columns([2, 2, 1])
                    with col1:
                        st.text(row['Full Name'])
                    with col2:
                        st.text(row['Short Form'])
                    with col3:
                        if st.button('🗑️', key=f"delete_{idx}"):
                            mappings_df = mappings_df.drop(idx)
                            save_mappings(mappings_df)
                            st.rerun()
                    st.sidebar.markdown("---")  # Add separator between entries
            
            # Show count of mappings
            total_count = len(mappings_df)
            filtered_count = len(filtered_df)
            if filter_text:
                st.sidebar.caption(f"Showing {filtered_count} of {total_count} mappings")
            else:
                st.sidebar.caption(f"Total mappings: {total_count}")
    else:
        st.sidebar.info("No mappings available")

    return mappings_df

def pdf_to_image(pdf_bytes):
    try:
        pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
        if pdf_document.page_count == 0:
            st.error("Uploaded PDF is empty.")
            return None

        page = pdf_document.load_page(0)
        zoom = 4
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")
        pdf_document.close()
        return img_bytes
    except Exception as e:
        st.error(f"Error converting PDF to image: {e}")
        return None

def call_gemini_api(image_bytes, prompt):
    if not st.session_state.api_key:
        st.error("Please enter your Gemini API key.")
        return None

    genai.configure(api_key=st.session_state.api_key)
    model = genai.GenerativeModel('gemini-2.0-flash', generation_config=genai.GenerationConfig(temperature=0.0))

    image_parts = [{"mime_type": "image/png", "data": base64.b64encode(image_bytes).decode("utf-8")}]
    prompt_parts = [prompt, image_parts[0]]
    
    try:
        response = model.generate_content(prompt_parts)
        return response.text.strip()
    except Exception as e:
        st.error(f"Error calling Gemini API: {e}")
        return None

def sanitize_filename(filename):
    invalid_chars = r'[\/*?:"<>|]'
    return re.sub(invalid_chars, '_', filename)

def generate_filename(key_identifier: str, payer: str, payee: str, currency: str, is_trailer_fee: bool, is_management_fee: bool) -> str:
    sanitized_payee = sanitize_filename(payee)

    # Check for trailer fee using AI's judgment
    if is_trailer_fee:
        if payer == "WEALTH MANAGEMENT CUBE LIMITED":
            return f"{key_identifier} WMC-{sanitized_payee}_T.pdf"
        elif payer == "WMC NOMINEE LIMITED-CLIENT TRUST ACCOUNT":
            return f"{currency} {key_identifier} {sanitized_payee}_T.pdf"
        else:
            return f"{sanitized_payee}_{key_identifier}_{currency}_T.pdf"
    
    # Check for management fee using AI's judgment
    elif is_management_fee and payee.upper() in ['OFS', 'OREANA FINANCIAL SERVICES LIMITED']:
        if payer == "WEALTH MANAGEMENT CUBE LIMITED":
            return f"{key_identifier} WMC-{sanitized_payee} MF.pdf"
        elif payer == "WMC NOMINEE LIMITED-CLIENT TRUST ACCOUNT":
            return f"{currency} {key_identifier} {sanitized_payee} MF.pdf"
        else:
            return f"{sanitized_payee}_{key_identifier}_{currency} MF.pdf"
    
    # Default naming without special suffixes
    else:
        if payer == "WEALTH MANAGEMENT CUBE LIMITED":
            return f"{key_identifier} WMC-{sanitized_payee}.pdf"
        elif payer == "WMC NOMINEE LIMITED-CLIENT TRUST ACCOUNT":
            return f"{currency} {key_identifier} {sanitized_payee}.pdf"
        else:
            return f"{sanitized_payee}_{key_identifier}_{currency}.pdf"

def main():
    st.title("Gemini Vision E-cheque UAT")

    # Load and display mapping manager in sidebar
    mappings_df = show_mapping_manager()

    if "api_key" not in st.session_state:
        st.session_state.api_key = ""
    st.session_state.api_key = st.text_input("Enter your Gemini API key:",
                                           value=st.session_state.api_key,
                                           type="password",
                                           key="api_key_input")

    uploaded_file = st.file_uploader("Upload an e-cheque PDF", type="pdf")

    if uploaded_file:
        pdf_bytes = uploaded_file.read()
        image_bytes = pdf_to_image(pdf_bytes)
        if not image_bytes:
            return

        image = Image.open(io.BytesIO(image_bytes))
        st.image(image, caption="Uploaded E-cheque (First Page)", use_container_width=True)

        prompt_choice = st.radio("Choose a prompt option:", ["Use Default Prompt", "Enter Custom Prompt"])
        prompt = st.text_area("Enter your custom prompt:", height=300) if prompt_choice == "Enter Custom Prompt" else generate_prompt()

        if st.button("Process E-cheque"):
            if not prompt.strip():
                st.error("Please enter a prompt.")
                return

            with st.spinner("Calling Gemini API..."):
                raw_response = call_gemini_api(image_bytes, prompt)

            if raw_response:
                try:
                    # Clean the response string
                    clean_response = raw_response.strip()
                    if clean_response.startswith("```json"):
                        clean_response = clean_response[7:-3]
                    
                    parsed_json = json.loads(clean_response)
                    
                    st.write("### Raw JSON Response from Gemini:")
                    st.code(json.dumps(parsed_json, indent=2), language="json")

                    if all(key in parsed_json for key in ["date", "payee", "key_identifier", "payer", "currency", "is_trailer_fee", "is_management_fee"]):
                        original_payee = parsed_json['payee']
                        shortened_payee = get_payee_shortform(original_payee, mappings_df)
                        key_identifier = parsed_json['key_identifier']
                        payer = parsed_json['payer']
                        currency = parsed_json['currency']
                        is_trailer_fee = parsed_json['is_trailer_fee']
                        is_management_fee = parsed_json['is_management_fee']
                        remarks = parsed_json.get('remarks', '')

                        filename = generate_filename(
                            key_identifier=key_identifier,
                            payer=payer,
                            payee=shortened_payee,
                            currency=currency,
                            is_trailer_fee=is_trailer_fee,
                            is_management_fee=is_management_fee
                        )

                        st.write("### Original Payee:", original_payee)
                        st.write("### Mapped Payee:", shortened_payee)
                        st.write("### Remarks:", remarks)
                        st.write("### Is Trailer Fee:", is_trailer_fee)
                        st.write("### Is Management Fee:", is_management_fee)
                        st.write("### Generated Filename:", filename)

                        st.download_button(
                            label="Download Renamed PDF",
                            data=pdf_bytes,
                            file_name=filename,
                            mime="application/pdf"
                        )

                        next_step = parsed_json.get("next_step")
                        st.write(f"### Next Step: {next_step}")

                    else:
                        st.error("Could not generate filename due to missing fields.")

                except json.JSONDecodeError as e:
                    st.error(f"Error parsing JSON: {str(e)}")
                    st.text("Raw response:")
                    st.code(raw_response)
            else:
                st.error("No response from Gemini API.")

if __name__ == "__main__":
    main()
