/*
	Copyright (C) 2003-2005 Daniel Muller, dan at verliba dot cz
	Copyright (C) 2006-2026 Verlihub Team, info at verlihub dot net

	Verlihub is free software; You can redistribute it
	and modify it under the terms of the GNU General
	Public License as published by the Free Software
	Foundation, either version 3 of the license, or at
	your option any later version.

	Verlihub is distributed in the hope that it will be
	useful, but without any warranty, without even the
	implied warranty of merchantability or fitness for
	a particular purpose. See the GNU General Public
	License for more details.

	Please see http://www.gnu.org/licenses/ for a copy
	of the GNU General Public License.
*/

#include "thread_safe_collections.h"
// Note: cuser.h include will be added when we integrate with existing code
// #include "cuser.h"

namespace nVerliHub {

// ============================================================================
// ThreadSafeUserCollection implementation
// ============================================================================

bool ThreadSafeUserCollection::AddUser(std::string_view nick, cUser* user) {
    if (!user) {
        return false;
    }
    
    std::unique_lock lock(m_mutex);
    
    auto [it, inserted] = m_users.try_emplace(std::string(nick), user);
    if (inserted) {
        m_count.fetch_add(1, std::memory_order_release);
    }
    
    return inserted;
}

cUser* ThreadSafeUserCollection::RemoveUser(std::string_view nick) {
    std::unique_lock lock(m_mutex);
    
    auto it = m_users.find(std::string(nick));
    if (it == m_users.end()) {
        return nullptr;
    }
    
    cUser* user = it->second;
    m_users.erase(it);
    m_count.fetch_sub(1, std::memory_order_release);
    
    return user;
}

bool ThreadSafeUserCollection::DeleteUser(std::string_view nick) {
    cUser* user = RemoveUser(nick);
    if (user) {
        delete user;
        return true;
    }
    return false;
}

cUser* ThreadSafeUserCollection::FindUser(std::string_view nick) const {
    std::shared_lock lock(m_mutex);
    
    auto it = m_users.find(std::string(nick));
    if (it != m_users.end()) {
        return it->second;
    }
    return nullptr;
}

bool ThreadSafeUserCollection::Contains(std::string_view nick) const {
    std::shared_lock lock(m_mutex);
    return m_users.contains(std::string(nick));
}

std::vector<std::string> ThreadSafeUserCollection::GetNicks() const {
    std::shared_lock lock(m_mutex);
    
    std::vector<std::string> nicks;
    nicks.reserve(m_users.size());
    
    for (const auto& [nick, _] : m_users) {
        nicks.push_back(nick);
    }
    
    return nicks;
}

void ThreadSafeUserCollection::Clear() {
    std::unique_lock lock(m_mutex);
    
    for (auto& [_, user] : m_users) {
        delete user;
    }
    m_users.clear();
    m_count.store(0, std::memory_order_release);
}

}  // namespace nVerliHub
