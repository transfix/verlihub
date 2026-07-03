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

#ifndef THREAD_SAFE_COLLECTIONS_H
#define THREAD_SAFE_COLLECTIONS_H

#include <shared_mutex>
#include <mutex>
#include <unordered_map>
#include <vector>
#include <functional>
#include <optional>
#include <concepts>
#include <atomic>
#include <string>
#include <ranges>
#include <algorithm>

namespace nVerliHub {

// Forward declarations
class cUser;

// ============================================================================
// Concepts for type constraints
// ============================================================================

/**
 * Concept: Types that can be used as map keys (must be hashable)
 */
template<typename T>
concept Hashable = requires(T a) {
    { std::hash<T>{}(a) } -> std::convertible_to<std::size_t>;
};

/**
 * Concept: Types that can be equality compared
 */
template<typename T>
concept EqualityComparable = requires(T a, T b) {
    { a == b } -> std::convertible_to<bool>;
};

/**
 * Concept: Valid callback for ForEach operations
 */
template<typename F, typename T>
concept ForEachCallback = std::invocable<F, T>;

/**
 * Concept: Valid predicate for filtering
 */
template<typename F, typename T>
concept Predicate = std::predicate<F, T>;

// ============================================================================
// ThreadSafeMap - Generic thread-safe hash map with reader-writer locking
// ============================================================================

/**
 * Thread-safe hash map using shared_mutex for reader-writer locking.
 * 
 * Features:
 * - Multiple concurrent readers
 * - Exclusive writer access
 * - Copy-on-read semantics (values are copied out)
 * - Callback-based iteration with lock held
 * 
 * @tparam K Key type (must satisfy Hashable and EqualityComparable)
 * @tparam V Value type
 */
template<Hashable K, typename V>
    requires EqualityComparable<K>
class ThreadSafeMap {
public:
    ThreadSafeMap() = default;
    ~ThreadSafeMap() = default;
    
    // Non-copyable, non-movable (contains mutex)
    ThreadSafeMap(const ThreadSafeMap&) = delete;
    ThreadSafeMap& operator=(const ThreadSafeMap&) = delete;
    ThreadSafeMap(ThreadSafeMap&&) = delete;
    ThreadSafeMap& operator=(ThreadSafeMap&&) = delete;

    /**
     * Insert or update a key-value pair.
     * 
     * @param key The key
     * @param value The value (copied)
     */
    void Put(const K& key, const V& value) {
        std::unique_lock lock(m_mutex);
        m_map[key] = value;
    }
    
    /**
     * Insert or update with move semantics.
     */
    void Put(const K& key, V&& value) {
        std::unique_lock lock(m_mutex);
        m_map[key] = std::move(value);
    }
    
    /**
     * Emplace a new element (construct in-place).
     * 
     * @return true if inserted, false if key already existed
     */
    template<typename... Args>
    bool Emplace(const K& key, Args&&... args) {
        std::unique_lock lock(m_mutex);
        auto [it, inserted] = m_map.try_emplace(key, std::forward<Args>(args)...);
        return inserted;
    }
    
    /**
     * Get a copy of the value for a key.
     * 
     * @param key The key to look up
     * @return std::optional containing value copy, or nullopt if not found
     */
    [[nodiscard]] std::optional<V> Get(const K& key) const {
        std::shared_lock lock(m_mutex);
        if (auto it = m_map.find(key); it != m_map.end()) {
            return it->second;
        }
        return std::nullopt;
    }
    
    /**
     * Get value or default if not found.
     */
    [[nodiscard]] V GetOr(const K& key, const V& default_value) const {
        std::shared_lock lock(m_mutex);
        if (auto it = m_map.find(key); it != m_map.end()) {
            return it->second;
        }
        return default_value;
    }
    
    /**
     * Remove an entry by key.
     * 
     * @return true if entry was removed, false if key not found
     */
    bool Remove(const K& key) {
        std::unique_lock lock(m_mutex);
        return m_map.erase(key) > 0;
    }
    
    /**
     * Remove and return an entry.
     * 
     * @return The removed value, or nullopt if not found
     */
    [[nodiscard]] std::optional<V> Take(const K& key) {
        std::unique_lock lock(m_mutex);
        if (auto it = m_map.find(key); it != m_map.end()) {
            V value = std::move(it->second);
            m_map.erase(it);
            return value;
        }
        return std::nullopt;
    }
    
    /**
     * Check if key exists.
     */
    [[nodiscard]] bool Contains(const K& key) const {
        std::shared_lock lock(m_mutex);
        return m_map.contains(key);
    }
    
    /**
     * Get current size.
     */
    [[nodiscard]] std::size_t Size() const {
        std::shared_lock lock(m_mutex);
        return m_map.size();
    }
    
    /**
     * Check if empty.
     */
    [[nodiscard]] bool Empty() const {
        std::shared_lock lock(m_mutex);
        return m_map.empty();
    }
    
    /**
     * Get all keys (snapshot copy).
     */
    [[nodiscard]] std::vector<K> Keys() const {
        std::shared_lock lock(m_mutex);
        std::vector<K> keys;
        keys.reserve(m_map.size());
        for (const auto& [key, _] : m_map) {
            keys.push_back(key);
        }
        return keys;
    }
    
    /**
     * Get all values (snapshot copy).
     */
    [[nodiscard]] std::vector<V> Values() const {
        std::shared_lock lock(m_mutex);
        std::vector<V> values;
        values.reserve(m_map.size());
        for (const auto& [_, value] : m_map) {
            values.push_back(value);
        }
        return values;
    }
    
    /**
     * Execute callback for each entry (with read lock held).
     * 
     * @param callback Function called as callback(key, value)
     */
    template<typename F>
        requires std::invocable<F, const K&, const V&>
    void ForEach(F&& callback) const {
        std::shared_lock lock(m_mutex);
        for (const auto& [key, value] : m_map) {
            callback(key, value);
        }
    }
    
    /**
     * Execute callback for each entry (with write lock, allows modification).
     */
    template<typename F>
        requires std::invocable<F, const K&, V&>
    void ForEachMut(F&& callback) {
        std::unique_lock lock(m_mutex);
        for (auto& [key, value] : m_map) {
            callback(key, value);
        }
    }
    
    /**
     * Find entries matching a predicate (returns copies).
     */
    template<Predicate<const V&> P>
    [[nodiscard]] std::vector<std::pair<K, V>> FindIf(P&& predicate) const {
        std::shared_lock lock(m_mutex);
        std::vector<std::pair<K, V>> results;
        for (const auto& [key, value] : m_map) {
            if (predicate(value)) {
                results.emplace_back(key, value);
            }
        }
        return results;
    }
    
    /**
     * Clear all entries.
     */
    void Clear() {
        std::unique_lock lock(m_mutex);
        m_map.clear();
    }
    
    /**
     * Update a value atomically using a callback.
     * 
     * @param key The key to update
     * @param updater Function called as updater(value) to modify the value
     * @return true if key was found and updated
     */
    template<typename F>
        requires std::invocable<F, V&>
    bool Update(const K& key, F&& updater) {
        std::unique_lock lock(m_mutex);
        if (auto it = m_map.find(key); it != m_map.end()) {
            updater(it->second);
            return true;
        }
        return false;
    }

private:
    mutable std::shared_mutex m_mutex;
    std::unordered_map<K, V> m_map;
};

// ============================================================================
// ThreadSafeUserCollection - Specialized collection for cUser pointers
// ============================================================================

/**
 * Thread-safe collection for managing online users.
 * 
 * This collection:
 * - Owns the cUser pointers (deletes on removal)
 * - Provides lock-free user count via atomic
 * - Supports filtered iteration by user class
 * - Uses nick as primary key (case-sensitive)
 * 
 * Thread Safety Guarantees:
 * - AddUser/RemoveUser: Exclusive access
 * - FindUser: Returns borrowed pointer (valid only while lock would be held)
 * - GetNicks/Size: Snapshot or atomic reads
 * - ForEach: Callback executes with read lock
 */
class ThreadSafeUserCollection {
public:
    ThreadSafeUserCollection() = default;
    
    ~ThreadSafeUserCollection() {
        Clear();
    }
    
    // Non-copyable, non-movable
    ThreadSafeUserCollection(const ThreadSafeUserCollection&) = delete;
    ThreadSafeUserCollection& operator=(const ThreadSafeUserCollection&) = delete;
    ThreadSafeUserCollection(ThreadSafeUserCollection&&) = delete;
    ThreadSafeUserCollection& operator=(ThreadSafeUserCollection&&) = delete;
    
    /**
     * Add a user to the collection.
     * 
     * @param nick User's nickname (key)
     * @param user Pointer to user object (collection takes ownership)
     * @return true if added, false if nick already exists
     */
    bool AddUser(std::string_view nick, cUser* user);
    
    /**
     * Remove a user from the collection.
     * 
     * @param nick Nickname to remove
     * @return Pointer to removed user (caller takes ownership), or nullptr
     */
    [[nodiscard]] cUser* RemoveUser(std::string_view nick);
    
    /**
     * Remove and delete a user.
     * 
     * @return true if user was found and deleted
     */
    bool DeleteUser(std::string_view nick);
    
    /**
     * Find a user by nickname.
     * 
     * WARNING: Returns a borrowed pointer. The pointer may become invalid
     * after the call returns if another thread removes the user.
     * Use ForEach for safe iteration.
     * 
     * @return Pointer to user, or nullptr if not found
     */
    [[nodiscard]] cUser* FindUser(std::string_view nick) const;
    
    /**
     * Check if a user exists.
     */
    [[nodiscard]] bool Contains(std::string_view nick) const;
    
    /**
     * Get user count (lock-free atomic read).
     */
    [[nodiscard]] std::size_t Size() const noexcept {
        return m_count.load(std::memory_order_acquire);
    }
    
    /**
     * Check if collection is empty.
     */
    [[nodiscard]] bool Empty() const noexcept {
        return Size() == 0;
    }
    
    /**
     * Get snapshot of all nicknames.
     */
    [[nodiscard]] std::vector<std::string> GetNicks() const;
    
    /**
     * Execute callback for each user (with read lock).
     * 
     * @param callback Function called as callback(user)
     */
    template<typename F>
        requires std::invocable<F, cUser*>
    void ForEach(F&& callback) const {
        std::shared_lock lock(m_mutex);
        for (const auto& [_, user] : m_users) {
            callback(user);
        }
    }
    
    /**
     * Execute callback for users in a class range.
     * 
     * @param callback Function to call
     * @param min_class Minimum user class (inclusive)
     * @param max_class Maximum user class (inclusive)
     */
    template<typename F>
        requires std::invocable<F, cUser*>
    void ForEachInClass(F&& callback, int min_class, int max_class) const {
        std::shared_lock lock(m_mutex);
        for (const auto& [_, user] : m_users) {
            // TODO: Check user class when cUser is refactored
            // For now, call for all users
            callback(user);
        }
    }
    
    /**
     * Find users matching a predicate.
     * 
     * @return Vector of matching nicknames (safe copies)
     */
    template<Predicate<cUser*> P>
    [[nodiscard]] std::vector<std::string> FindIf(P&& predicate) const {
        std::shared_lock lock(m_mutex);
        std::vector<std::string> results;
        for (const auto& [nick, user] : m_users) {
            if (predicate(user)) {
                results.push_back(nick);
            }
        }
        return results;
    }
    
    /**
     * Count users matching a predicate.
     */
    template<Predicate<cUser*> P>
    [[nodiscard]] std::size_t CountIf(P&& predicate) const {
        std::shared_lock lock(m_mutex);
        return std::ranges::count_if(m_users | std::views::values, predicate);
    }
    
    /**
     * Clear all users (deletes all user objects).
     */
    void Clear();

private:
    mutable std::shared_mutex m_mutex;
    std::unordered_map<std::string, cUser*> m_users;
    std::atomic<std::size_t> m_count{0};
};

// ============================================================================
// LockFreeCounter - Atomic counter with wait/notify support
// ============================================================================

/**
 * Lock-free counter using C++20 atomic wait/notify.
 * 
 * Useful for:
 * - User counts
 * - Share size totals
 * - Connection statistics
 */
template<std::integral T>
class LockFreeCounter {
public:
    explicit LockFreeCounter(T initial = 0) noexcept 
        : m_value(initial) {}
    
    /**
     * Get current value.
     */
    [[nodiscard]] T Get() const noexcept {
        return m_value.load(std::memory_order_acquire);
    }
    
    /**
     * Set to specific value.
     */
    void Set(T value) noexcept {
        m_value.store(value, std::memory_order_release);
        m_value.notify_all();
    }
    
    /**
     * Increment and return new value.
     */
    T Increment(T delta = 1) noexcept {
        T result = m_value.fetch_add(delta, std::memory_order_acq_rel) + delta;
        m_value.notify_all();
        return result;
    }
    
    /**
     * Decrement and return new value.
     */
    T Decrement(T delta = 1) noexcept {
        T result = m_value.fetch_sub(delta, std::memory_order_acq_rel) - delta;
        m_value.notify_all();
        return result;
    }
    
    /**
     * Wait until value changes from expected.
     */
    void WaitUntilNot(T expected) const noexcept {
        m_value.wait(expected, std::memory_order_acquire);
    }
    
    /**
     * Wait until value equals expected.
     */
    void WaitUntil(T expected) const noexcept {
        T current = m_value.load(std::memory_order_acquire);
        while (current != expected) {
            m_value.wait(current, std::memory_order_acquire);
            current = m_value.load(std::memory_order_acquire);
        }
    }
    
    // Implicit conversion to T
    operator T() const noexcept { return Get(); }
    
    // Pre-increment
    T operator++() noexcept { return Increment(); }
    
    // Pre-decrement  
    T operator--() noexcept { return Decrement(); }
    
    // Addition assignment
    T operator+=(T delta) noexcept { return Increment(delta); }
    
    // Subtraction assignment
    T operator-=(T delta) noexcept { return Decrement(delta); }

private:
    std::atomic<T> m_value;
};

// ============================================================================
// EventFlag - Thread synchronization primitive using C++20 atomics
// ============================================================================

/**
 * Simple event flag for thread signaling.
 * 
 * Uses C++20 atomic wait/notify for efficient blocking.
 */
class EventFlag {
public:
    EventFlag() = default;
    
    /**
     * Signal the event (wake all waiters).
     */
    void Signal() noexcept {
        m_flag.store(true, std::memory_order_release);
        m_flag.notify_all();
    }
    
    /**
     * Wait for the event to be signaled.
     */
    void Wait() const noexcept {
        m_flag.wait(false, std::memory_order_acquire);
    }
    
    /**
     * Reset the event to non-signaled state.
     */
    void Reset() noexcept {
        m_flag.store(false, std::memory_order_release);
    }
    
    /**
     * Check if signaled (non-blocking).
     */
    [[nodiscard]] bool IsSignaled() const noexcept {
        return m_flag.load(std::memory_order_acquire);
    }
    
    /**
     * Signal, execute callback, then reset.
     */
    template<typename F>
        requires std::invocable<F>
    void SignalAndReset(F&& callback) {
        Signal();
        callback();
        Reset();
    }

private:
    std::atomic<bool> m_flag{false};
};

// ============================================================================
// Type aliases for common use cases
// ============================================================================

using StringMap = ThreadSafeMap<std::string, std::string>;
using IntMap = ThreadSafeMap<std::string, int>;
using UserCount = LockFreeCounter<std::size_t>;
using ShareSize = LockFreeCounter<std::uint64_t>;

}  // namespace nVerliHub

#endif  // THREAD_SAFE_COLLECTIONS_H
