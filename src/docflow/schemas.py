"""Provider extraction schemas kept separate from transport code."""

FORM_Z2_EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": "Fields printed on a Kazakhstan Form Z-2 inventory issue note.",
    "properties": {
        "supplier_name": {
            "type": "string",
            "description": "Supplier name exactly as printed; omit when absent and do not infer.",
        },
        "supplier_tax_id": {
            "type": "string",
            "description": (
                "Supplier BIN or tax identifier exactly as printed; preserve leading zeroes, "
                "omit when absent, and do not infer."
            ),
        },
        "buyer_name": {
            "type": "string",
            "description": "Buyer name exactly as printed; omit when absent and do not infer.",
        },
        "buyer_tax_id": {
            "type": "string",
            "description": (
                "Buyer BIN or tax identifier exactly as printed; preserve leading zeroes, "
                "omit when absent, and do not infer."
            ),
        },
        "document_number": {
            "type": "string",
            "description": (
                "Document number exactly as printed; preserve leading zeroes and do not infer."
            ),
        },
        "document_date": {
            "type": "string",
            "description": "Document date exactly as printed; omit when absent and do not infer.",
        },
        "currency": {
            "type": "string",
            "description": "Currency code or label exactly as printed; do not infer from locale.",
        },
        "responsible_person": {
            "type": "string",
            "description": "Responsible person exactly as printed; omit when absent.",
        },
        "line_items": {
            "type": "array",
            "description": "One object per printed line-item row, in source order.",
            "items": {
                "type": "object",
                "properties": {
                    "line_number": {
                        "type": "string",
                        "description": "Printed row number as a string; preserve leading zeroes.",
                    },
                    "product_description": {
                        "type": "string",
                        "description": "Product description exactly as printed; do not correct it.",
                    },
                    "sku": {
                        "type": "string",
                        "description": (
                            "SKU exactly as printed; preserve leading zeroes and omit when absent."
                        ),
                    },
                    "unit": {
                        "type": "string",
                        "description": "Unit of measure exactly as printed; omit when absent.",
                    },
                    "quantity": {
                        "type": "string",
                        "description": (
                            "Quantity exactly as printed, including its decimal separator; "
                            "do not calculate."
                        ),
                    },
                    "unit_price": {
                        "type": "string",
                        "description": (
                            "Unit price exactly as printed, including separators; do not calculate."
                        ),
                    },
                    "vat_amount": {
                        "type": "string",
                        "description": (
                            "VAT amount exactly as printed; omit when absent and do not calculate."
                        ),
                    },
                    "line_total": {
                        "type": "string",
                        "description": "Line total exactly as printed; do not calculate.",
                    },
                },
            },
        },
        "subtotal": {
            "type": "string",
            "description": "Subtotal exactly as printed; omit when absent and do not calculate.",
        },
        "vat_total": {
            "type": "string",
            "description": "Total VAT exactly as printed; omit when absent and do not calculate.",
        },
        "grand_total": {
            "type": "string",
            "description": "Grand total exactly as printed; omit when absent and do not calculate.",
        },
    },
}

FORM_Z2_EXTRACTION_INSTRUCTIONS = (
    "Extract only values explicitly present in the document. Do not infer, calculate, "
    "semantically correct, or fill missing values. Preserve identifier leading zeroes, "
    "printed number formatting, line-item row order, and one output item per source row."
)
