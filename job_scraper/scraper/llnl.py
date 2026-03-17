from job_scraper.scraper._smartrecruiters import scrape_board

scrape = scrape_board("LLNL", name="Lawrence Livermore National Lab")

if __name__ == "__main__":
    from job_scraper.scraper import run

    run(scrape)
