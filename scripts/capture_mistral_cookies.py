#!/usr/bin/env python3
"""Script to capture Mistral account cookies and subscription data.

Usage:
    python scripts/capture_mistral_cookies.py              # Capture all accounts from open Chrome
    python scripts/capture_mistral_cookies.py --list       # List saved accounts
    python scripts/capture_mistral_cookies.py --delete     # Delete all saved accounts
    python scripts/capture_mistral_cookies.py --test       # Test with current Chrome session
"""
import argparse
import json
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from mistral_cookies import (
    capture_account_from_browser,
    list_saved_mistral_accounts,
    delete_mistral_cookies,
    get_all_account_data,
)


def main():
    parser = argparse.ArgumentParser(description='Manage Mistral account cookies for AI Usage Dashboard')
    parser.add_argument('--list', action='store_true', help='List all saved Mistral accounts')
    parser.add_argument('--delete', action='store_true', help='Delete all saved Mistral accounts')
    parser.add_argument('--test', action='store_true', help='Test cookie capture from current Chrome')
    parser.add_argument('--show', action='store_true', help='Show subscription data for all saved accounts')
    parser.add_argument('--capture', action='store_true', help='Capture accounts from open Chrome (default)')
    
    args = parser.parse_args()
    
    if args.list:
        accounts = list_saved_mistral_accounts()
        if accounts:
            print("Saved Mistral accounts:")
            for i, account in enumerate(accounts, 1):
                print(f"  {i}. {account}")
        else:
            print("No saved Mistral accounts.")
        return
    
    if args.delete:
        accounts = list_saved_mistral_accounts()
        for account in accounts:
            delete_mistral_cookies(account)
            print(f"Deleted: {account}")
        print("All Mistral accounts deleted.")
        return
    
    if args.test or args.capture or not any(vars(args).values()):
        print("Capturing Mistral accounts from Chrome...")
        try:
            data = capture_account_from_browser()
            if data:
                if isinstance(data, list):
                    print(f"\nCaptured {len(data)} account(s):")
                    for i, account in enumerate(data, 1):
                        sub = account.get('subscription', {})
                        print(f"  {i}. {account.get('account')} - {sub.get('plan', 'Unknown')} ({sub.get('status', 'Unknown')})")
                        print(f"     API: ${sub.get('api_used', 0):.2f}/${sub.get('api_allowance', 0):.2f} ({sub.get('api_usage_percent', 0):.1f}%)")
                        print(f"     Vibe: ${sub.get('vibe_used', 0):.2f}/${sub.get('vibe_allowance', 0):.2f} ({sub.get('vibe_usage_percent', 0):.1f}%)")
                else:
                    sub = data.get('subscription', {})
                    print(f"\nCaptured: {data.get('account')} - {sub.get('plan', 'Unknown')}")
                    print(f"  API: ${sub.get('api_used', 0):.2f}/${sub.get('api_allowance', 0):.2f} ({sub.get('api_usage_percent', 0):.1f}%)")
                    print(f"  Vibe: ${sub.get('vibe_used', 0):.2f}/${sub.get('vibe_allowance', 0):.2f} ({sub.get('vibe_usage_percent', 0):.1f}%)")
            else:
                print("No Mistral accounts found in Chrome. Make sure you're logged in to Mistral admin.")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        return
    
    if args.show:
        print("Subscription data for all saved accounts:")
        try:
            all_data = get_all_account_data()
            if all_data:
                for account_data in all_data:
                    sub = account_data.get('subscription', {})
                    print(f"\n{account_data.get('account')}: {sub.get('plan', 'Unknown')} ({sub.get('status', 'Unknown')})")
                    print(f"  Free until: {sub.get('free_until', 'N/A')}")
                    print(f"  API: ${sub.get('api_used', 0):.2f}/${sub.get('api_allowance', 0):.2f} ({sub.get('api_usage_percent', 0):.1f}%)")
                    print(f"  Vibe: ${sub.get('vibe_used', 0):.2f}/${sub.get('vibe_allowance', 0):.2f} ({sub.get('vibe_usage_percent', 0):.1f}%)")
                    print(f"  Reset: {sub.get('reset_in', 'N/A')}")
            else:
                print("No saved Mistral accounts with subscription data.")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        return
    
    parser.print_help()


if __name__ == '__main__':
    main()
