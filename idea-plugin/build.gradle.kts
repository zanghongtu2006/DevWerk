import org.gradle.jvm.tasks.Jar

plugins {
    id("java")
    kotlin("jvm") version "1.9.23"
    id("org.jetbrains.intellij") version "1.17.4"
}

group = "com.zanghongtu"
version = "0.0.1"

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.json:json:20250517")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}

intellij {
    version.set("2024.1")
    type.set("IC")
    plugins.set(listOf("java"))
}

tasks {
    patchPluginXml {
        sinceBuild.set("241")
        untilBuild.set("243.*")
    }

    compileKotlin {
        kotlinOptions.jvmTarget = "17"
    }

    compileJava {
        targetCompatibility = "17"
        sourceCompatibility = "17"
    }

    runIde {
        dependsOn("ensureCoroutinesJavaAgent")
        // Keep the default sandbox and IDE runtime behavior.
    }
}

tasks.register<Jar>("ensureCoroutinesJavaAgent") {
    archiveFileName.set("coroutines-javaagent.jar")
    destinationDirectory.set(layout.buildDirectory.dir("tmp/initializeIntelliJPlugin"))
    manifest {
        attributes(
            mapOf(
                "Premain-Class" to "kotlinx.coroutines.debug.AgentPremain",
                "Can-Retransform-Classes" to "true",
                "Multi-Release" to "true",
            )
        )
    }
    mustRunAfter("initializeIntelliJPlugin")
}
