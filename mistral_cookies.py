"""Mistral cookies management for multi-account support.

This module provides functionality to:
1. Extract Mistral cookies from Chrome using browser_cookie3
2. Save cookies to a secure file for persistence
3. Load cookies from file without requiring Chrome to be open
4. Support multiple Mistral accounts
"""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# Directory for storing Mistral cookie files
COOKIES_DIR = Path.home() / ".config" / "ai_usage_dashboard" / "mistral_cookies"
# File extension for cookie files
COOKIE_FILE_EXT = ".cookies.json"


def _ensure_cookies_dir() -> None:
    """Create the cookies directory with secure permissions."""
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    # Set restrictive permissions (owner read/write only)
    os.chmod(COOKIES_DIR, stat.S_IRWXU)


def _get_cookie_file_path(account_name: str) -> Path:
    """Get the path for an account's cookie file."""
    _ensure_cookies_dir()
    # Sanitize account name for filename
    safe_name = re.sub(r'[^a-zA-Z0-9_@.-]', '_', account_name)
    return COOKIES_DIR / f"{safe_name}{COOKIE_FILE_EXT}"


def extract_mistral_cookies_from_chrome() -> dict[str, str]:
    """Extract all Mistral cookies from Chrome and return them as a single dict.
    
    Uses browser_cookie3 to read cookies from Chrome's Default profile.
    Combines cookies from all Mistral domains (auth.mistral.ai, docs.mistral.ai, .mistral.ai, etc.)
    into a single cookie dict that can be used for requests to admin.mistral.ai.
    
    Returns:
        dict mapping cookie names to values (combined from all Mistral domains)
    """
    try:
        import browser_cookie3
        all_cookies = browser_cookie3.chrome()
        mistral_cookies = [
            c for c in all_cookies 
            if 'mistral' in (c.domain or '').lower()
        ]
        
        # Combine all cookies into one dict
        # Cookies from .mistral.ai will work for admin.mistral.ai
        cookies: dict[str, str] = {}
        for cookie in mistral_cookies:
            cookies[cookie.name] = cookie.value
        
        return cookies
    except Exception:
        return {}


def save_mistral_cookies(account_name: str, cookies: dict[str, str]) -> Path:
    """Save Mistral cookies for an account to a file.
    
    Args:
        account_name: Account identifier (email or name)
        cookies: Dict of cookie name -> value
        
    Returns:
        Path to the saved cookie file
    """
    _ensure_cookies_dir()
    filepath = _get_cookie_file_path(account_name)
    
    # Add metadata
    data = {
        "account": account_name,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "cookies": cookies,
        "version": 1
    }
    
    # Write with secure permissions
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(filepath, stat.S_IRUSR | stat.S_IWUSR)  # Owner read/write only
    
    return filepath


def load_mistral_cookies(account_name: str) -> dict[str, str] | None:
    """Load Mistral cookies for an account from file.
    
    Args:
        account_name: Account identifier (email or name)
        
    Returns:
        Dict of cookie name -> value, or None if not found
    """
    filepath = _get_cookie_file_path(account_name)
    if not filepath.exists():
        return None
    
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return data.get("cookies", {})
    except (json.JSONDecodeError, OSError):
        return None


def list_saved_mistral_accounts() -> list[str]:
    """List all saved Mistral account names."""
    if not COOKIES_DIR.exists():
        return []
    
    accounts = []
    for filepath in COOKIES_DIR.glob(f"*{COOKIE_FILE_EXT}"):
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            accounts.append(data.get("account", filepath.stem))
        except (json.JSONDecodeError, OSError):
            continue
    
    return sorted(accounts)


def delete_mistral_cookies(account_name: str) -> bool:
    """Delete saved cookies for an account."""
    filepath = _get_cookie_file_path(account_name)
    if filepath.exists():
        filepath.unlink()
        return True
    return False


def fetch_subscription_data(cookies: dict[str, str]) -> dict[str, Any] | None:
    """Fetch subscription data from Mistral admin using cookies.
    
    Args:
        cookies: Dict of cookie name -> value
        
    Returns:
        Parsed subscription data or None on failure
    """
    url = 'https://admin.mistral.ai/subscription'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    
    try:
        response = requests.get(url, headers=headers, cookies=cookies, timeout=15)
        response.raise_for_status()
        html = response.text
        return parse_subscription_html(html)
    except Exception:
        return None


def parse_subscription_html(html: str) -> dict[str, Any]:
    """Parse subscription data from Mistral admin HTML page."""
    data: dict[str, Any] = {}
    
    # Extract email - try multiple patterns, excluding known false positives
    email_patterns = [
        r'"email":\s*"([^"]+)"',
        r'"user":\s*\{.*"email":\s*"([^"]+)"',
        r'data-email="([^"]+)"',
        r'data-user-email="([^"]+)"',
        r'email="([^"]+)"',
    ]
    
    for pattern in email_patterns:
        email_match = re.search(pattern, html, re.DOTALL)
        if email_match:
            email = email_match.group(1)
            # Filter out known false positives (Sentry, etc.)
            if not any(x in email.lower() for x in ['sentry.io', 'ingest.', '@o', 'sentry', 'segment']):
                data['email'] = email
                break
    
    # If still no email, try to find it in text near user/account info
    if not data.get('email'):
        # Look for email in specific contexts
        context_patterns = [
            r'(?:Account|User|Email|Profile|Welcome)[^>]*[^<]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
            r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})[^<]*[^>]*(?:Account|User|Profile)',
        ]
        for pattern in context_patterns:
            email_match = re.search(pattern, html, re.IGNORECASE)
            if email_match:
                email = email_match.group(1)
                if not any(x in email.lower() for x in ['sentry.io', 'ingest.', '@o', 'sentry', 'segment']):
                    data['email'] = email
                    break
    
    # Extract plan
    plan_match = re.search(r'Current plan[^<]*<[^>]+>([^<]+)', html, re.IGNORECASE)
    if not plan_match:
        plan_match = re.search(r'>(Pro|Plus|Free|Enterprise)<', html)
    if plan_match:
        data['plan'] = plan_match.group(1).strip()
    
    # Extract status
    status_match = re.search(r'>(Active|Inactive)<', html)
    if status_match:
        data['status'] = status_match.group(1)
    
    # Extract free until
    free_match = re.search(r'Free with your offer code until ([^<]+)', html)
    if free_match:
        data['free_until'] = free_match.group(1).strip()
    
    # Extract subscription price
    sub_match = re.search(r'Subscription[^$]*\$([\d.,]+)', html)
    if sub_match:
        data['subscription_price'] = float(sub_match.group(1).replace(',', ''))
    
    # Extract estimated total
    total_match = re.search(r'Estimated total[^$]*\$([\d.,]+)', html)
    if total_match:
        data['estimated_total'] = float(total_match.group(1).replace(',', ''))
    
    # Extract reset time
    reset_match = re.search(r'Resets in ([^<]+)', html)
    if reset_match:
        data['reset_in'] = reset_match.group(1).strip()
    
    # Extract API and Vibe usage from dollar amount pairs
    # Look for pairs of dollar amounts in tabular-nums spans
    pairs = re.findall(
        r'<span[^>]*class="[^"]*tabular-nums[^"]*"[^>]*>\$([\d.,]+)</span>.*?<span[^>]*class="[^"]*tabular-nums[^"]*"[^>]*>\$([\d.,]+)</span>',
        html,
        re.DOTALL
    )
    
    if pairs:
        if len(pairs) >= 1:
            data['api_used'] = float(pairs[0][0].replace(',', ''))
            data['api_allowance'] = float(pairs[0][1].replace(',', ''))
            if data['api_allowance'] > 0:
                data['api_usage_percent'] = round(data['api_used'] / data['api_allowance'] * 100, 2)
        
        if len(pairs) >= 2:
            data['vibe_used'] = float(pairs[1][0].replace(',', ''))
            data['vibe_allowance'] = float(pairs[1][1].replace(',', ''))
            if data['vibe_allowance'] > 0:
                data['vibe_usage_percent'] = round(data['vibe_used'] / data['vibe_allowance'] * 100, 2)
    
    return data


def capture_account_from_browser(account_name: str | None = None) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Capture Mistral account data from currently open Chrome browser.
    
    This function:
    1. Extracts all Mistral cookies from Chrome
    2. Fetches subscription data using combined cookies
    3. Tries to extract email from the subscription page
    4. Saves cookies to file with email as account name
    
    Args:
        account_name: Optional specific account to capture. If None, captures all.
        
    Returns:
        If account_name is specified: single account dict or None
        If account_name is None: list of account dicts (one per logged-in account) or None
    """
    cookies = extract_mistral_cookies_from_chrome()
    if not cookies:
        return None
    
    # Fetch subscription data with all cookies
    sub_data = fetch_subscription_data(cookies)
    if not sub_data:
        return None
    
    # Try to get email from subscription data or cookies
    email = sub_data.get('email')
    if not email:
        # Try to find email in cookie values
        for name, value in cookies.items():
            if '@' in value and re.match(r'^[^@]+@[^@]+\.[^@]+$', value):
                email = value
                break
    
    # If still no email, use a default name
    if not email:
        email = "mistral_account_1"
    
    if account_name and email != account_name:
        return None
    
    # Save cookies with the email as account name
    save_mistral_cookies(email, cookies)
    
    # Combine with subscription data
    result = {
        "account": email,
        "cookies": cookies,
        "subscription": sub_data,
        "captured_at": datetime.now(timezone.utc).isoformat()
    }
    
    return result


def get_all_account_data() -> list[dict[str, Any]]:
    """Get subscription data for all saved Mistral accounts.
    
    Returns:
        List of account data dicts with subscription info
    """
    results = []
    saved_accounts = list_saved_mistral_accounts()
    
    for account_name in saved_accounts:
        cookies = load_mistral_cookies(account_name)
        if cookies:
            sub_data = fetch_subscription_data(cookies)
            if sub_data:
                results.append({
                    "account": account_name,
                    "subscription": sub_data
                })
    
    return results


def format_for_dashboard(account_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Format account data for the dashboard.
    
    Args:
        account_data: Dict with 'account' and 'subscription' keys
        
    Returns:
        List of row dicts formatted for dashboard display (one per quota window)
    """
    sub = account_data.get('subscription', {})
    email = account_data.get('account', '')
    plan = sub.get('plan', 'unknown')
    
    # Parse reset_in to timestamp if it's a relative time like "in 11 days" or "11 days"
    reset_in = sub.get('reset_in', '')
    next_reset_ms = None
    next_reset_iso = None
    if reset_in:
        try:
            from datetime import timedelta
            import re
            # Try to parse "in N days" or "N days" format
            match = re.match(r'(?:in\s+)?(\d+)\s+days?', reset_in.lower())
            if match:
                days = int(match.group(1))
                reset_dt = datetime.now(timezone.utc) + timedelta(days=days)
                next_reset_ms = int(reset_dt.timestamp() * 1000)
                next_reset_iso = reset_dt.isoformat(timespec='seconds')
        except Exception:
            pass
    
    rows = []
    
    # API quota row
    if sub.get('api_usage_percent') is not None:
        api_pct = round(sub.get('api_usage_percent', 0))
        rows.append({
            "provider": "mistral_subscription",
            "account": email,
            "account_label": email,
            "label": "API",
            "plan_type": plan,
            "status": "ok",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "percentage": api_pct,
            "used_percentage": api_pct,
            "next_reset_time_ms": next_reset_ms,
            "next_reset_iso": next_reset_iso or sub.get('reset_in'),
            "detail": {
                "plan": plan,
                "status": sub.get('status'),
                "email": email,
                "subscription_price": sub.get('subscription_price'),
                "estimated_total": sub.get('estimated_total'),
                "api_used": sub.get('api_used'),
                "api_allowance": sub.get('api_allowance'),
                "api_usage_percent": sub.get('api_usage_percent'),
                "vibe_used": sub.get('vibe_used'),
                "vibe_allowance": sub.get('vibe_allowance'),
                "vibe_usage_percent": sub.get('vibe_usage_percent'),
                "reset_in": sub.get('reset_in'),
                "reset_iso": sub.get('reset_in'),
                "reset_time_ms": next_reset_ms,
            },
        })
    
    # Vibe quota row
    if sub.get('vibe_usage_percent') is not None:
        vibe_pct = round(sub.get('vibe_usage_percent', 0))
        rows.append({
            "provider": "mistral_subscription",
            "account": email,
            "account_label": email,
            "label": "Vibe",
            "plan_type": plan,
            "status": "ok",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "percentage": vibe_pct,
            "used_percentage": vibe_pct,
            "next_reset_time_ms": next_reset_ms,
            "next_reset_iso": next_reset_iso or sub.get('reset_in'),
            "detail": {
                "plan": plan,
                "status": sub.get('status'),
                "email": email,
                "subscription_price": sub.get('subscription_price'),
                "estimated_total": sub.get('estimated_total'),
                "api_used": sub.get('api_used'),
                "api_allowance": sub.get('api_allowance'),
                "api_usage_percent": sub.get('api_usage_percent'),
                "vibe_used": sub.get('vibe_used'),
                "vibe_allowance": sub.get('vibe_allowance'),
                "vibe_usage_percent": sub.get('vibe_usage_percent'),
                "reset_in": sub.get('reset_in'),
                "reset_iso": sub.get('reset_in'),
                "reset_time_ms": next_reset_ms,
            },
        })
    
    return rows
