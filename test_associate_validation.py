import sys
import os

# Ensure backend directory is in python path so imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from models.associate import AssociateModel
from pydantic import ValidationError

def test_associate_model():
    print("Testing AssociateModel with valid data (including empty email and extra fields)...")
    payload = {
        "id": "12345",
        "name": "Jane Doe",
        "phone_number": "555-0000",
        "email_id": "",  # Empty email to test validator
        "primary_role": "Editor",
        "employment_type": "In-house",
        "some_extra_field": "Should be ignored"
    }

    try:
        model = AssociateModel(**payload)
        print("Success! Model validated payload.")
        print(f"Parsed id: {model.id}")
        print(f"Parsed email_id: {model.email_id} (Expected None)")
        
        if model.email_id is not None:
            print("ERROR: email_id was not converted to None.")
            sys.exit(1)
            
    except ValidationError as e:
        print("Validation Error!")
        print(e)
        sys.exit(1)

if __name__ == "__main__":
    test_associate_model()
