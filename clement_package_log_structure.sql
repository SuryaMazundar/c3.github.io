-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: 127.0.0.1
-- Generation Time: Feb 14, 2026 at 07:21 AM
-- Server version: 10.4.32-MariaDB
-- PHP Version: 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `clement_package_log`
--

-- --------------------------------------------------------

--
-- Table structure for table `audit_logs`
--

CREATE TABLE `audit_logs` (
  `id` int(11) NOT NULL,
  `user_id` int(11) DEFAULT NULL,
  `action` varchar(100) DEFAULT NULL,
  `description` text DEFAULT NULL,
  `ip_address` varchar(45) DEFAULT NULL,
  `user_agent` text DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `halls`
--

CREATE TABLE `halls` (
  `id` int(11) NOT NULL,
  `hall_name` varchar(100) NOT NULL,
  `hall_code` varchar(10) NOT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `initialscheck`
--

CREATE TABLE `initialscheck` (
  `id` int(11) NOT NULL,
  `fullName` varchar(50) NOT NULL,
  `title` varchar(50) NOT NULL,
  `initials` varchar(10) NOT NULL,
  `hall_id` int(11) DEFAULT NULL,
  `username` varchar(50) DEFAULT NULL,
  `password_hash` varchar(255) DEFAULT NULL,
  `temporary_password` tinyint(1) DEFAULT 1,
  `is_active` tinyint(1) DEFAULT 1,
  `can_checkin` tinyint(1) DEFAULT 1,
  `can_checkout` tinyint(1) DEFAULT 1,
  `can_view_other_halls` tinyint(1) DEFAULT 0,
  `can_manage_users` tinyint(1) DEFAULT 0,
  `created_by` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `last_password_change` timestamp NULL DEFAULT NULL,
  `can_manage_halls` tinyint(1) DEFAULT 0,
  `can_manage_shifts` tinyint(1) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `package_log`
--

CREATE TABLE `package_log` (
  `ID` int(11) NOT NULL,
  `TrackingID` varchar(300) NOT NULL,
  `DateTime` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `postofficelog`
--

CREATE TABLE `postofficelog` (
  `Id` int(11) NOT NULL,
  `trackingId` varchar(100) NOT NULL,
  `roomNumber` varchar(50) NOT NULL,
  `checkInDate` datetime NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  `type` varchar(30) NOT NULL,
  `checkInEmpInitials` varchar(100) NOT NULL,
  `checkoutStatus` varchar(1) NOT NULL DEFAULT '0',
  `checkoutDate` datetime DEFAULT NULL,
  `checkoutEmpType` varchar(10) NOT NULL,
  `checkoutEmpInitials` varchar(100) DEFAULT NULL,
  `perishable` varchar(5) NOT NULL,
  `notes` text NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `return_to_sender`
--

CREATE TABLE `return_to_sender` (
  `id` int(11) NOT NULL,
  `postoffice_log_id` int(11) NOT NULL,
  `tracking_id` varchar(255) DEFAULT NULL,
  `last_name` varchar(100) DEFAULT NULL,
  `first_name` varchar(100) DEFAULT NULL,
  `room` varchar(50) DEFAULT NULL,
  `rts_type` enum('Return to Sender','Forwarding') DEFAULT NULL,
  `address` text DEFAULT NULL,
  `date_submitted` date DEFAULT NULL,
  `title_initials` varchar(50) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `shift_change_requests`
--

CREATE TABLE `shift_change_requests` (
  `id` int(11) NOT NULL,
  `hall_id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `message` text NOT NULL,
  `status` varchar(20) DEFAULT 'open',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `shift_exceptions`
--

CREATE TABLE `shift_exceptions` (
  `id` int(11) NOT NULL,
  `schedule_id` int(11) NOT NULL,
  `original_user_id` int(11) DEFAULT NULL,
  `replacement_user_id` int(11) DEFAULT NULL,
  `exception_date` date NOT NULL,
  `reason` text DEFAULT NULL,
  `approved_by` int(11) DEFAULT NULL,
  `approved_at` timestamp NULL DEFAULT NULL,
  `created_by` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `shift_schedule`
--

CREATE TABLE `shift_schedule` (
  `id` int(11) NOT NULL,
  `hall_id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `shift_date` date NOT NULL,
  `start_time` time NOT NULL,
  `end_time` time NOT NULL,
  `shift_type` varchar(50) DEFAULT 'Regular',
  `notes` text DEFAULT NULL,
  `created_by` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `shift_templates`
--

CREATE TABLE `shift_templates` (
  `id` int(11) NOT NULL,
  `hall_id` int(11) NOT NULL,
  `template_name` varchar(100) NOT NULL,
  `start_time` time NOT NULL,
  `end_time` time NOT NULL,
  `created_by` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `studentmaster`
--

CREATE TABLE `studentmaster` (
  `Id` bigint(20) UNSIGNED NOT NULL,
  `firstName` varchar(30) NOT NULL,
  `lastName` varchar(30) NOT NULL,
  `preferredName` varchar(30) NOT NULL,
  `roomNumber` varchar(30) NOT NULL,
  `hallName` varchar(50) NOT NULL,
  `academicYear` varchar(20) NOT NULL,
  `email` varchar(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Indexes for dumped tables
--

--
-- Indexes for table `audit_logs`
--
ALTER TABLE `audit_logs`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `halls`
--
ALTER TABLE `halls`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `hall_name` (`hall_name`),
  ADD UNIQUE KEY `hall_code` (`hall_code`);

--
-- Indexes for table `initialscheck`
--
ALTER TABLE `initialscheck`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `idx_username` (`username`),
  ADD UNIQUE KEY `username` (`username`),
  ADD KEY `fk_initialscheck_hall_id` (`hall_id`);

--
-- Indexes for table `package_log`
--
ALTER TABLE `package_log`
  ADD PRIMARY KEY (`ID`);

--
-- Indexes for table `postofficelog`
--
ALTER TABLE `postofficelog`
  ADD PRIMARY KEY (`Id`);

--
-- Indexes for table `return_to_sender`
--
ALTER TABLE `return_to_sender`
  ADD PRIMARY KEY (`id`),
  ADD KEY `fk_rts_packagelog` (`postoffice_log_id`);

--
-- Indexes for table `shift_change_requests`
--
ALTER TABLE `shift_change_requests`
  ADD PRIMARY KEY (`id`);

--
-- Indexes for table `shift_exceptions`
--
ALTER TABLE `shift_exceptions`
  ADD PRIMARY KEY (`id`),
  ADD KEY `schedule_id` (`schedule_id`),
  ADD KEY `original_user_id` (`original_user_id`),
  ADD KEY `replacement_user_id` (`replacement_user_id`),
  ADD KEY `approved_by` (`approved_by`),
  ADD KEY `created_by` (`created_by`);

--
-- Indexes for table `shift_schedule`
--
ALTER TABLE `shift_schedule`
  ADD PRIMARY KEY (`id`),
  ADD KEY `created_by` (`created_by`),
  ADD KEY `idx_hall_date` (`hall_id`,`shift_date`),
  ADD KEY `idx_user_date` (`user_id`,`shift_date`);

--
-- Indexes for table `shift_templates`
--
ALTER TABLE `shift_templates`
  ADD PRIMARY KEY (`id`),
  ADD KEY `hall_id` (`hall_id`),
  ADD KEY `created_by` (`created_by`);

--
-- Indexes for table `studentmaster`
--
ALTER TABLE `studentmaster`
  ADD PRIMARY KEY (`Id`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `audit_logs`
--
ALTER TABLE `audit_logs`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `halls`
--
ALTER TABLE `halls`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `initialscheck`
--
ALTER TABLE `initialscheck`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `package_log`
--
ALTER TABLE `package_log`
  MODIFY `ID` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `postofficelog`
--
ALTER TABLE `postofficelog`
  MODIFY `Id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `return_to_sender`
--
ALTER TABLE `return_to_sender`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `shift_change_requests`
--
ALTER TABLE `shift_change_requests`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `shift_exceptions`
--
ALTER TABLE `shift_exceptions`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `shift_schedule`
--
ALTER TABLE `shift_schedule`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `shift_templates`
--
ALTER TABLE `shift_templates`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `studentmaster`
--
ALTER TABLE `studentmaster`
  MODIFY `Id` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `initialscheck`
--
ALTER TABLE `initialscheck`
  ADD CONSTRAINT `fk_initialscheck_hall_id` FOREIGN KEY (`hall_id`) REFERENCES `halls` (`id`) ON DELETE SET NULL;

--
-- Constraints for table `return_to_sender`
--
ALTER TABLE `return_to_sender`
  ADD CONSTRAINT `fk_rts_packagelog` FOREIGN KEY (`postoffice_log_id`) REFERENCES `package_log` (`ID`) ON DELETE CASCADE;

--
-- Constraints for table `shift_exceptions`
--
ALTER TABLE `shift_exceptions`
  ADD CONSTRAINT `shift_exceptions_ibfk_1` FOREIGN KEY (`schedule_id`) REFERENCES `shift_schedule` (`id`),
  ADD CONSTRAINT `shift_exceptions_ibfk_2` FOREIGN KEY (`original_user_id`) REFERENCES `initialscheck` (`id`),
  ADD CONSTRAINT `shift_exceptions_ibfk_3` FOREIGN KEY (`replacement_user_id`) REFERENCES `initialscheck` (`id`),
  ADD CONSTRAINT `shift_exceptions_ibfk_4` FOREIGN KEY (`approved_by`) REFERENCES `initialscheck` (`id`),
  ADD CONSTRAINT `shift_exceptions_ibfk_5` FOREIGN KEY (`created_by`) REFERENCES `initialscheck` (`id`);

--
-- Constraints for table `shift_schedule`
--
ALTER TABLE `shift_schedule`
  ADD CONSTRAINT `shift_schedule_ibfk_1` FOREIGN KEY (`hall_id`) REFERENCES `halls` (`id`),
  ADD CONSTRAINT `shift_schedule_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `initialscheck` (`id`),
  ADD CONSTRAINT `shift_schedule_ibfk_3` FOREIGN KEY (`created_by`) REFERENCES `initialscheck` (`id`);

--
-- Constraints for table `shift_templates`
--
ALTER TABLE `shift_templates`
  ADD CONSTRAINT `shift_templates_ibfk_1` FOREIGN KEY (`hall_id`) REFERENCES `halls` (`id`),
  ADD CONSTRAINT `shift_templates_ibfk_2` FOREIGN KEY (`created_by`) REFERENCES `initialscheck` (`id`);
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
