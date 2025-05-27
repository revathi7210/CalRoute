import re

def extract_place_name(address_string):
    """
    Extract just the place name from an address string.
    
    Examples:
    - "Starbucks, 123 Main St, Irvine CA" -> "Starbucks"
    - "123 Main St, Irvine CA" -> "123 Main St"
    - "Walmart Supercenter at 456 Broadway" -> "Walmart Supercenter"
    """
    if not address_string:
        return ""
    
    # First check for common patterns with commas
    comma_parts = address_string.split(',')
    if len(comma_parts) > 1:
        # Often the place name is before the first comma
        return comma_parts[0].strip()
    
    # Check for "at" pattern (e.g., "Starbucks at 123 Main St")
    at_parts = address_string.split(' at ')
    if len(at_parts) > 1:
        return at_parts[0].strip()
    
    # Check for common address patterns
    address_pattern = r'^(\d+\s+[A-Za-z\s]+)'
    match = re.match(address_pattern, address_string)
    if match:
        # This is likely just a street address without a business name
        return address_string.strip()
    
    # If we can't identify a specific pattern, return the first few words
    # which are likely to be the place name
    words = address_string.split()
    if len(words) > 3:
        return ' '.join(words[:3]).strip()
    
    return address_string.strip()
